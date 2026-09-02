"""Approval-gated actions. Agents only CREATE pending approvals; execution happens here, only for an
'approved' row, only when the owner decides. Executors are registered per action name; an agent package
(the Foundry, a built agent) can register its own."""
from __future__ import annotations

import os
import smtplib
from email.message import EmailMessage
from typing import Callable

from .ledger import Ledger
from .store import Store

EXECUTORS: dict[str, Callable] = {}


def executor(action: str):
    def deco(fn):
        EXECUTORS[action] = fn
        return fn
    return deco


def describe(action: str, target: str, payload: dict | None) -> str:
    fn = EXECUTORS.get(action)
    doc = (fn.__doc__ or "").strip().splitlines()[0] if fn else "no executor registered (approval will be recorded only)"
    return f"{action} → {target}: {doc}"


def decide(store: Store, ledger: Ledger, aid: int, approve: bool, who: str = "owner") -> dict:
    a = store.get_approval(aid)
    if not a:
        raise KeyError(aid)
    if a["status"] != "pending":
        raise ValueError(f"approval {aid} is {a['status']}, not pending")
    store.decide_approval(aid, "approved" if approve else "denied")
    ledger.append("approval_decided", a.get("run_id"), approval_id=aid, action=a["action"], target=a["target"],
                  decision="approved" if approve else "denied", by=who)
    return store.get_approval(aid)


def execute(cfg, store: Store, ledger: Ledger, aid: int, dry_run: bool = False) -> dict:
    a = store.get_approval(aid)
    if not a:
        raise KeyError(aid)
    if a["status"] != "approved":
        raise ValueError(f"approval {aid} is {a['status']}; only 'approved' actions execute")
    fn = EXECUTORS.get(a["action"])
    if dry_run:
        ledger.append("approval_dry_run", a.get("run_id"), approval_id=aid, action=a["action"], target=a["target"])
        return {"approval": a, "dry_run": True, "description": describe(a["action"], a["target"], a.get("payload"))}
    if not fn:
        store.record_execution(aid, "executed", "recorded: no executor registered for this action")
        ledger.append("approval_executed", a.get("run_id"), approval_id=aid, action=a["action"], target=a["target"], ok=True, result="recorded only")
        return {"approval": store.get_approval(aid), "ok": True, "result": "recorded only"}
    try:
        ok, result = fn(cfg, store, ledger, a)
    except Exception as e:  # noqa: BLE001
        ok, result = False, f"{type(e).__name__}: {e}"
    store.record_execution(aid, "executed" if ok else "failed", str(result)[:1000])
    ledger.append("approval_executed", a.get("run_id"), approval_id=aid, action=a["action"], target=a["target"], ok=ok, result=str(result)[:300])
    return {"approval": store.get_approval(aid), "ok": ok, "result": result}


# ---- built-in executors
@executor("send_email")
def _send_email(cfg, store: Store, ledger: Ledger, a: dict):
    """Send the approved outbox draft over SMTP (env SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASSWORD, SMTP_FROM)."""
    draft = store.get("outbox", a["target"])
    if not draft:
        return False, f"draft {a['target']} not found"
    host, user, pwd, sender = (os.environ.get(k) for k in ("SMTP_HOST", "SMTP_USER", "SMTP_PASSWORD", "SMTP_FROM"))
    if not (host and user and pwd and sender):
        store.put("outbox", a["target"], {**{k: v for k, v in draft.items() if not k.startswith("_") and k != "id"}, "status": "approved_not_sent"})
        return False, "SMTP not configured (set SMTP_HOST/SMTP_PORT/SMTP_USER/SMTP_PASSWORD/SMTP_FROM); draft kept as approved_not_sent"
    if not draft.get("to") or "@" not in str(draft.get("to")):
        return False, f"recipient address missing or invalid: {draft.get('to')!r}"
    msg = EmailMessage()
    msg["From"], msg["To"], msg["Subject"] = sender, draft["to"], draft["subject"]
    msg.set_content(draft["body"])
    port = int(os.environ.get("SMTP_PORT", "587"))
    with smtplib.SMTP(host, port, timeout=30) as s:
        s.starttls()
        s.login(user, pwd)
        s.send_message(msg)
    store.put("outbox", a["target"], {**{k: v for k, v in draft.items() if not k.startswith("_") and k != "id"}, "status": "sent"})
    return True, f"sent to {draft['to']} via {host}"
