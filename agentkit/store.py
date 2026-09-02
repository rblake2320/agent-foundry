"""SQLite persistence: runs, approvals, budget, and a small JSON document store for agent records."""
from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
  id TEXT PRIMARY KEY, started_at TEXT NOT NULL, ended_at TEXT, mode TEXT NOT NULL, status TEXT NOT NULL,
  halt_reason TEXT, phase TEXT, receipt TEXT, summary TEXT, report_path TEXT
);
CREATE TABLE IF NOT EXISTS approvals (
  id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT, created_at TEXT NOT NULL, action TEXT NOT NULL, target TEXT NOT NULL,
  payload TEXT, rationale TEXT, status TEXT NOT NULL DEFAULT 'pending', decided_at TEXT, executed_at TEXT, result TEXT
);
CREATE TABLE IF NOT EXISTS budget (month TEXT PRIMARY KEY, model_calls INTEGER DEFAULT 0, tokens_in INTEGER DEFAULT 0, tokens_out INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS docs (
  collection TEXT NOT NULL, id TEXT NOT NULL, body TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
  PRIMARY KEY (collection, id)
);
"""


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _row(r) -> dict | None:
    if r is None:
        return None
    d = dict(r)
    for k in ("receipt", "payload"):
        if isinstance(d.get(k), str):
            try:
                d[k] = json.loads(d[k])
            except json.JSONDecodeError:
                pass
    return d


class Store:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.conn() as c:
            c.executescript(SCHEMA)

    @contextmanager
    def conn(self):
        c = sqlite3.connect(self.path, timeout=30, check_same_thread=False)
        c.row_factory = sqlite3.Row
        try:
            yield c
            c.commit()
        finally:
            c.close()

    # ---- runs
    def create_run(self, run_id: str, mode: str) -> None:
        with self.conn() as c:
            c.execute("INSERT INTO runs(id, started_at, mode, status, phase) VALUES (?,?,?,?,?)", (run_id, now(), mode, "running", "DEFINE"))

    def set_phase(self, run_id: str, phase: str) -> None:
        with self.conn() as c:
            c.execute("UPDATE runs SET phase=? WHERE id=?", (phase, run_id))

    def finish_run(self, run_id: str, status: str, halt_reason: str, receipt: dict, summary: str | None, report_path: str | None) -> None:
        with self.conn() as c:
            c.execute("UPDATE runs SET ended_at=?, status=?, halt_reason=?, receipt=?, summary=?, report_path=?, phase='HALT' WHERE id=?",
                      (now(), status, halt_reason, json.dumps(receipt, default=str), summary, report_path, run_id))

    def get_run(self, run_id: str) -> dict | None:
        with self.conn() as c:
            return _row(c.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone())

    def list_runs(self, limit: int = 50) -> list[dict]:
        with self.conn() as c:
            return [_row(r) for r in c.execute("SELECT * FROM runs ORDER BY started_at DESC LIMIT ?", (limit,))]

    def running_run(self) -> dict | None:
        with self.conn() as c:
            return _row(c.execute("SELECT * FROM runs WHERE status='running' ORDER BY started_at DESC LIMIT 1").fetchone())

    # ---- approvals
    def create_approval(self, run_id: str | None, action: str, target: str, rationale: str, payload: dict | None = None) -> int:
        with self.conn() as c:
            dup = c.execute("SELECT id FROM approvals WHERE action=? AND target=? AND status IN ('pending','approved')", (action, target)).fetchone()
            if dup:
                return int(dup["id"])
            cur = c.execute("INSERT INTO approvals(run_id, created_at, action, target, payload, rationale) VALUES (?,?,?,?,?,?)",
                            (run_id, now(), action, target, json.dumps(payload or {}, default=str), rationale))
            return int(cur.lastrowid)

    def list_approvals(self, status: str | None = None) -> list[dict]:
        with self.conn() as c:
            q = "SELECT * FROM approvals" + (" WHERE status=?" if status else "") + " ORDER BY id DESC"
            return [_row(r) for r in c.execute(q, (status,) if status else ())]

    def get_approval(self, aid: int) -> dict | None:
        with self.conn() as c:
            return _row(c.execute("SELECT * FROM approvals WHERE id=?", (aid,)).fetchone())

    def decide_approval(self, aid: int, status: str) -> None:
        with self.conn() as c:
            c.execute("UPDATE approvals SET status=?, decided_at=? WHERE id=?", (status, now(), aid))

    def record_execution(self, aid: int, status: str, result: str) -> None:
        with self.conn() as c:
            c.execute("UPDATE approvals SET status=?, executed_at=?, result=? WHERE id=?", (status, now(), result, aid))

    # ---- budget
    def month_budget(self) -> dict:
        month = datetime.now(timezone.utc).strftime("%Y-%m")
        with self.conn() as c:
            r = c.execute("SELECT * FROM budget WHERE month=?", (month,)).fetchone()
            return _row(r) or {"month": month, "model_calls": 0, "tokens_in": 0, "tokens_out": 0}

    def add_budget(self, calls: int, tin: int, tout: int) -> None:
        month = datetime.now(timezone.utc).strftime("%Y-%m")
        with self.conn() as c:
            c.execute("""INSERT INTO budget(month, model_calls, tokens_in, tokens_out) VALUES (?,?,?,?)
                         ON CONFLICT(month) DO UPDATE SET model_calls=model_calls+excluded.model_calls,
                         tokens_in=tokens_in+excluded.tokens_in, tokens_out=tokens_out+excluded.tokens_out""", (month, calls, tin, tout))

    # ---- documents
    def put(self, collection: str, doc_id: str, body: dict) -> dict:
        with self.conn() as c:
            old = c.execute("SELECT created_at FROM docs WHERE collection=? AND id=?", (collection, doc_id)).fetchone()
            created = old["created_at"] if old else now()
            c.execute("""INSERT INTO docs(collection, id, body, created_at, updated_at) VALUES (?,?,?,?,?)
                         ON CONFLICT(collection, id) DO UPDATE SET body=excluded.body, updated_at=excluded.updated_at""",
                      (collection, doc_id, json.dumps(body, default=str), created, now()))
        return {"id": doc_id, **body}

    def get(self, collection: str, doc_id: str) -> dict | None:
        with self.conn() as c:
            r = c.execute("SELECT * FROM docs WHERE collection=? AND id=?", (collection, doc_id)).fetchone()
            if not r:
                return None
            return {"id": r["id"], "_created_at": r["created_at"], "_updated_at": r["updated_at"], **json.loads(r["body"])}

    def list(self, collection: str, limit: int = 1000, **where) -> list[dict]:
        with self.conn() as c:
            rows = c.execute("SELECT * FROM docs WHERE collection=? ORDER BY updated_at DESC LIMIT ?", (collection, limit))
            out = []
            for r in rows:
                d = {"id": r["id"], "_created_at": r["created_at"], "_updated_at": r["updated_at"], **json.loads(r["body"])}
                if all(d.get(k) == v for k, v in where.items()):
                    out.append(d)
            return out

    def delete(self, collection: str, doc_id: str) -> None:
        with self.conn() as c:
            c.execute("DELETE FROM docs WHERE collection=? AND id=?", (collection, doc_id))

    def collections(self) -> list[str]:
        with self.conn() as c:
            return [r["collection"] for r in c.execute("SELECT DISTINCT collection FROM docs ORDER BY collection")]
