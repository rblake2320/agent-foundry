"""Record tools: the agent's own document store, notes, and scoped file reads."""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path

from . import tool

_SAFE_ID = re.compile(r"^[A-Za-z0-9._\-]{1,80}$")


def _slug(s: str) -> str:
    s = re.sub(r"[^A-Za-z0-9]+", "-", s.strip().lower()).strip("-")
    return s[:60] or "item"


@tool("record_put", "Create or update one record in a named collection (e.g. leads, quotes, notes). Fields is a JSON object.",
      {"collection": "collection name", "id": "record id (slug); omit to derive from fields.name", "fields": "JSON object of fields"}, risk="write")
def record_put(ctx, collection: str, fields, id: str | None = None) -> str:
    if isinstance(fields, str):
        fields = json.loads(fields)
    if not isinstance(fields, dict):
        return "ERROR: fields must be a JSON object"
    rid = id or _slug(str(fields.get("name") or fields.get("title") or fields.get("company") or datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")))
    if not _SAFE_ID.match(rid):
        rid = _slug(rid)
    existing = ctx.store.get(_slug(collection), rid) or {}
    merged = {k: v for k, v in existing.items() if not k.startswith("_") and k != "id"}
    merged.update(fields)
    merged.setdefault("created_by_run", ctx.run_id)
    merged["updated_by_run"] = ctx.run_id
    ctx.store.put(_slug(collection), rid, merged)
    ctx.ledger.append("record_put", ctx.run_id, collection=_slug(collection), id=rid, task=ctx.task)
    return f"saved {collection}/{rid}: {json.dumps(merged, default=str)[:400]}"


@tool("record_get", "Read one record by collection and id.", {"collection": "collection name", "id": "record id"})
def record_get(ctx, collection: str, id: str) -> str:
    d = ctx.store.get(_slug(collection), id)
    return json.dumps(d, default=str) if d else f"not found: {collection}/{id}"


@tool("record_list", "List records in a collection (newest first), optionally filtered by a field value.",
      {"collection": "collection name", "field": "optional field to filter on", "value": "optional value the field must equal", "limit": "default 25"})
def record_list(ctx, collection: str, field: str | None = None, value=None, limit: int = 25) -> str:
    rows = ctx.store.list(_slug(collection), limit=int(limit or 25), **({field: value} if field else {}))
    if not rows:
        return f"no records in {collection}" + (f" where {field}={value}" if field else "")
    return "\n".join(json.dumps({k: v for k, v in r.items() if not k.startswith("_")}, default=str)[:300] for r in rows)


@tool("note_write", "Append a dated note to the agent's notebook (data/notes.md). Use for observations worth keeping between runs.",
      {"text": "the note"}, risk="write")
def note_write(ctx, text: str) -> str:
    p = ctx.cfg.data_dir / "notes.md"
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "a", encoding="utf-8") as f:
        f.write(f"- {datetime.now(timezone.utc).isoformat(timespec='minutes')} [{ctx.task}] {text.strip()[:1000]}\n")
    return "note saved"


@tool("read_file", "Read a text file inside the agent's own folder (reports, data, skills, tasks). Paths outside are refused.",
      {"path": "relative path inside the agent folder", "max_chars": "default 6000"})
def read_file(ctx, path: str, max_chars: int = 6000) -> str:
    root = ctx.cfg.root.resolve()
    p = (root / path).resolve()
    if root not in p.parents and p != root:
        return "ERROR: path outside the agent folder"
    if not p.is_file():
        return f"ERROR: not a file: {path}"
    return p.read_text(encoding="utf-8", errors="replace")[: int(max_chars or 6000)]


@tool("current_time", "Current date and time (UTC).", {})
def current_time(ctx) -> str:
    return datetime.now(timezone.utc).isoformat(timespec="minutes")
