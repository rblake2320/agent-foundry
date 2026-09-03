#!/usr/bin/env python3
"""Build a realistic large agent state for load testing: copies an agent folder and grows its ledger and record store to
production-like sizes (default 50,000 hash-chained ledger events, 20,000 records across 3 collections, 2,000 runs, 500 approvals).
The ledger is written with the same chain rule as agentkit.ledger, so verify() still passes on the result.

  python scripts/make_load_fixture.py products/agent-seller <dest> [--events 50000] [--docs 20000]"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from agentkit.store import Store  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("src")
    ap.add_argument("dest")
    ap.add_argument("--events", type=int, default=50_000)
    ap.add_argument("--docs", type=int, default=20_000)
    ap.add_argument("--runs", type=int, default=2_000)
    ap.add_argument("--approvals", type=int, default=500)
    a = ap.parse_args()
    src, dest = Path(a.src), Path(a.dest)
    if dest.exists():
        shutil.rmtree(dest)
    shutil.copytree(src, dest, ignore=shutil.ignore_patterns("data", "reports", "__pycache__", ".cache"))
    (dest / "data").mkdir()
    (dest / "reports").mkdir()
    t0 = time.time()
    prev = "0" * 64
    ts = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with open(dest / "data" / "ledger.jsonl", "w", encoding="utf-8") as f:
        for i in range(a.events):
            row = {"ts": ts, "run_id": f"run-{i % a.runs:05d}", "event": ("tool_call", "model_step", "record_put", "run_finished")[i % 4],
                   "detail": {"tool": "web_search", "latency_s": 0.4, "i": i}, "prev_hash": prev}
            row["hash"] = hashlib.sha256((prev + json.dumps(row, sort_keys=True, separators=(",", ":"), ensure_ascii=False)).encode()).hexdigest()
            prev = row["hash"]
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    store = Store(dest / "data" / "agent.db")
    with store.conn() as c:
        c.executemany("INSERT INTO runs(id, started_at, ended_at, mode, status, halt_reason, phase, receipt) VALUES (?,?,?,?,?,?,?,?)",
                      [(f"run-{i:05d}", ts, ts, "all-tasks", "completed", "done", "HALT", json.dumps({"model": "m", "tasks": ["t"], "duration_s": 3})) for i in range(a.runs)])
        c.executemany("INSERT INTO approvals(run_id, created_at, action, target, payload, rationale, status) VALUES (?,?,?,?,?,?,?)",
                      [(f"run-{i % a.runs:05d}", ts, "send_email", f"draft-{i}", "{}", "r", ("pending", "executed", "denied")[i % 3]) for i in range(a.approvals)])
        c.executemany("INSERT INTO docs(collection, id, body, created_at, updated_at) VALUES (?,?,?,?,?)",
                      [(("leads", "quotes", "outbox")[i % 3], f"doc-{i}", json.dumps({"name": f"Company {i}", "status": "open", "created_by_run": f"run-{i % a.runs:05d}",
                                                                                       "lead_id": f"doc-{i - 1}", "notes": "x" * 200}), ts, ts) for i in range(a.docs)])
    print(f"fixture at {dest}: {a.events} ledger events, {a.docs} docs, {a.runs} runs, {a.approvals} approvals in {round(time.time() - t0, 1)}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
