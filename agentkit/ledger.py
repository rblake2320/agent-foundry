"""Append-only, hash-chained ledger. Every event hashes the previous one; verify() finds the first break."""
from __future__ import annotations

import hashlib
import json
import threading
from datetime import datetime, timezone
from pathlib import Path

_GENESIS = "0" * 64
_lock = threading.Lock()


def _h(prev: str, payload: dict) -> str:
    return hashlib.sha256((prev + json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)).encode()).hexdigest()


class Ledger:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def _last(self) -> str:
        if not self.path.exists():
            return _GENESIS
        last = _GENESIS
        with open(self.path, encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    last = json.loads(line)["hash"]
        return last

    def append(self, event: str, run_id: str | None = None, **detail) -> dict:
        with _lock:
            prev = self._last()
            row = {"ts": datetime.now(timezone.utc).isoformat(timespec="seconds"), "run_id": run_id,
                   "event": event, "detail": detail, "prev_hash": prev}
            row["hash"] = _h(prev, row)
            with open(self.path, "a", encoding="utf-8") as f:
                f.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")
            return row

    def read(self, limit: int = 200, run_id: str | None = None) -> list[dict]:
        if not self.path.exists():
            return []
        rows = []
        with open(self.path, encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    r = json.loads(line)
                    if run_id is None or r.get("run_id") == run_id:
                        rows.append(r)
        return rows[-limit:]

    def verify(self) -> dict:
        if not self.path.exists():
            return {"ok": True, "count": 0, "first_bad_line": None}
        prev, n = _GENESIS, 0
        with open(self.path, encoding="utf-8") as f:
            for i, line in enumerate(f, 1):
                if not line.strip():
                    continue
                r = json.loads(line)
                if r.get("prev_hash") != prev or r.get("hash") != _h(prev, {k: v for k, v in r.items() if k != "hash"}):
                    return {"ok": False, "count": n, "first_bad_line": i}
                prev, n = r["hash"], n + 1
        return {"ok": True, "count": n, "first_bad_line": None}
