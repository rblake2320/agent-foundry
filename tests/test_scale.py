"""Scale regressions: the failures the 2026-09-03 load test exposed must stay fixed. Real files, real SQLite, real HTTP through the app.
- ledger append/read must not grow with ledger size (was O(n): every append re-read the whole file; /api/status re-verified the whole chain)
- record listing filters in SQL and is bounded by LIMIT (was: load everything, filter in Python)
- Mission Control hot endpoints stay fast on a large state and identical reads are served from the 1 s cache
"""
import json
import time

from fastapi.testclient import TestClient

from agentkit.ledger import Ledger
from agentkit.mc import create_app
from agentkit.store import Store
from agentkit.worker import Worker

N_EVENTS = 20_000
N_DOCS = 10_000


def _grow(cfg, events=N_EVENTS, docs=N_DOCS):
    ledger = Ledger(cfg.ledger)
    ledger.append("seed", None)
    # write the chain directly (same rule as Ledger) so growing the fixture does not itself take O(n^2)
    import hashlib
    prev = ledger.read(limit=1)[0]["hash"]
    with open(cfg.ledger, "a", encoding="utf-8") as f:
        for i in range(events):
            row = {"ts": "2026-01-01T00:00:00+00:00", "run_id": f"run-{i % 500}", "event": "tool_call", "detail": {"i": i}, "prev_hash": prev}
            row["hash"] = hashlib.sha256((prev + json.dumps(row, sort_keys=True, separators=(",", ":"), ensure_ascii=False)).encode()).hexdigest()
            prev = row["hash"]
            f.write(json.dumps(row) + "\n")
    store = Store(cfg.db)
    with store.conn() as c:
        c.executemany("INSERT INTO docs(collection, id, body, created_at, updated_at) VALUES (?,?,?,?,?)",
                      [("leads", f"d{i}", json.dumps({"status": "open" if i % 10 else "won", "created_by_run": f"run-{i % 500}"}), "t", f"t{i:07d}") for i in range(docs)])
        c.executemany("INSERT INTO runs(id, started_at, mode, status) VALUES (?,?,?,?)", [(f"run-{i}", f"2026-01-01T00:{i % 60:02d}:00", "m", "completed") for i in range(500)])
    return Ledger(cfg.ledger), store


def test_ledger_append_and_tail_read_do_not_scale_with_size(cfg):
    ledger, _ = _grow(cfg)
    fresh = Ledger(cfg.ledger)           # no cached head: must find the chain head from the file tail, not by scanning
    t0 = time.perf_counter()
    for _ in range(50):
        fresh.append("probe", None)
    per_append_ms = (time.perf_counter() - t0) * 1000 / 50
    assert per_append_ms < 5, f"append is {per_append_ms:.1f} ms on {N_EVENTS} events (must be O(1))"
    t0 = time.perf_counter()
    rows = fresh.read(limit=200)
    assert len(rows) == 200 and rows[-1]["event"] == "probe" and (time.perf_counter() - t0) * 1000 < 20
    assert fresh.verify()["ok"] and fresh.count() == N_EVENTS + 51
    v = fresh.verify_cached()
    assert v["ok"] and v["cached"] and v["count"] == N_EVENTS + 51


def test_store_filters_in_sql_and_limit_bounds_results(cfg):
    _, store = _grow(cfg, events=10, docs=N_DOCS)
    assert store.count("leads") == N_DOCS and store.collection_counts() == {"leads": N_DOCS}
    t0 = time.perf_counter()
    won = store.list("leads", limit=50, status="won")
    assert len(won) == 50 and all(d["status"] == "won" for d in won) and (time.perf_counter() - t0) * 1000 < 100
    assert store.count_approvals("pending") == 0 and len(store.list_approvals(limit=10)) == 0


def test_mission_control_hot_paths_stay_fast_on_large_state(cfg):
    _grow(cfg)
    client = TestClient(create_app(cfg, Worker))
    for path in ("/api/status", "/api/activity?limit=200", "/api/runs?limit=50", "/api/docs/leads?limit=100", "/api/approvals", "/.well-known/agent-card.json"):
        t0 = time.perf_counter()
        r = client.get(path)
        ms = (time.perf_counter() - t0) * 1000
        assert r.status_code == 200, path
        assert ms < 250, f"{path} took {ms:.0f} ms on {N_EVENTS} events / {N_DOCS} docs"
        r2 = client.get(path)
        assert r2.status_code == 200 and r2.headers.get("x-cache") == "hit" and r2.content == r.content, f"{path} not served from cache"
    s = client.get("/api/status").json()
    assert s["collections"] == {"leads": N_DOCS} and s["ledger"]["ok"] and s["ledger"]["count"] == N_EVENTS + 1
    # a write invalidates cached reads
    client.put("/api/docs/leads/d1", json={"fields": {"status": "lost"}})
    assert client.get("/api/docs/leads?limit=100&status=lost").json()[0]["id"] == "d1"
