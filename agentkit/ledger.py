"""Append-only, hash-chained ledger. Every event hashes the previous one; verify() finds the first break.

Scale rules (measured, not assumed): append is O(1) — the chain head is cached in memory and re-read from the file TAIL only when
another process may have written; read(limit) reads from the tail, never the whole file; verify() is the one O(n) operation and is
never on a request hot path (Mission Control serves a cached verdict and refreshes it in the background)."""
from __future__ import annotations

import hashlib
import json
import os
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

_GENESIS = "0" * 64
_lock = threading.Lock()
_TAIL = 64 * 1024


def _h(prev: str, payload: dict) -> str:
    return hashlib.sha256((prev + json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)).encode()).hexdigest()


class Ledger:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._head: str | None = None
        self._head_size = -1          # file size the cached head corresponds to; a size change means someone else appended
        self._verify_cache: dict | None = None
        self._verify_at = 0.0

    # ---- tail reading
    def _tail_lines(self, want: int) -> list[str]:
        """Last `want` non-empty lines, reading backwards in chunks."""
        if not self.path.exists():
            return []
        with open(self.path, "rb") as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            chunk, buf = _TAIL, b""
            while size > 0:
                step = min(chunk, size)
                size -= step
                f.seek(size)
                buf = f.read(step) + buf
                if buf.count(b"\n") > want or size == 0:
                    break
                chunk *= 2
        lines = [ln for ln in buf.decode("utf-8", errors="replace").split("\n") if ln.strip()]
        if size > 0 and lines:
            lines = lines[1:]  # first piece may be a partial line
        return lines[-want:]

    def _last(self) -> str:
        size = self.path.stat().st_size if self.path.exists() else 0
        if self._head is not None and size == self._head_size:
            return self._head
        tail = self._tail_lines(1)
        self._head = json.loads(tail[0])["hash"] if tail else _GENESIS
        self._head_size = size
        return self._head

    def append(self, event: str, run_id: str | None = None, **detail) -> dict:
        with _lock:
            prev = self._last()
            row = {"ts": datetime.now(timezone.utc).isoformat(timespec="seconds"), "run_id": run_id,
                   "event": event, "detail": detail, "prev_hash": prev}
            row["hash"] = _h(prev, row)
            with open(self.path, "a", encoding="utf-8") as f:
                f.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")
            self._head, self._head_size = row["hash"], self.path.stat().st_size
            return row

    def read(self, limit: int = 200, run_id: str | None = None) -> list[dict]:
        if not self.path.exists():
            return []
        if run_id is None:
            return [json.loads(ln) for ln in self._tail_lines(limit)]
        rows: list[dict] = []
        with open(self.path, encoding="utf-8") as f:   # filtered read: a run's events are usually near the tail, but be complete
            for line in f:
                if line.strip():
                    r = json.loads(line)
                    if r.get("run_id") == run_id:
                        rows.append(r)
        return rows[-limit:]

    def count(self) -> int:
        if not self.path.exists():
            return 0
        with open(self.path, "rb") as f:
            return sum(1 for ln in f if ln.strip())

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

    def verify_cached(self, max_age_s: float = 60.0) -> dict:
        """Verdict for hot paths: full verify at most once per max_age_s, refreshed in a background thread; count is always live."""
        now = time.time()
        if self._verify_cache is None:
            self._verify_cache = {**self.verify(), "verified_at": now}
            self._verify_at = now
        elif now - self._verify_at > max_age_s:
            self._verify_at = now

            def refresh():
                self._verify_cache = {**self.verify(), "verified_at": time.time()}
            threading.Thread(target=refresh, daemon=True).start()
        return {**self._verify_cache, "count": self.count() if self._verify_cache["ok"] else self._verify_cache["count"], "cached": True}
