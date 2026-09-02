"""Sales tools: catalog lookup, pricing engine (2026 benchmarks), outreach drafts and quotes that only PROPOSE
approval-gated actions. Nothing here sends anything."""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path

from . import tool

# Public 2026 benchmarks (sources in the seller's SKILL.md): ready-made agents $20-100/user/mo;
# per task/conversation $0.05-0.50; per verified outcome ~$0.99 (Intercom Fin), $0.50-2.00 range;
# custom builds $20k-300k+. Hybrid = base subscription + usage/outcome layer (market direction).
PRICING_MODELS = {
    "per_seat": {"unit": "user/month", "low": 20.0, "mid": 49.0, "high": 100.0},
    "per_task": {"unit": "task", "low": 0.05, "mid": 0.20, "high": 0.50},
    "per_outcome": {"unit": "verified outcome", "low": 0.50, "mid": 0.99, "high": 2.00},
    "custom_build": {"unit": "one-time", "low": 20000.0, "mid": 45000.0, "high": 300000.0},
}


def load_catalog(ctx) -> list[dict]:
    """Catalog = the Foundry's registry, read from the path in [agent_data].catalog_path (JSON list)."""
    path = ctx.cfg.extra.get("catalog_path")
    if not path:
        return [d for d in ctx.store.list("catalog")]
    p = Path(path) if Path(path).is_absolute() else ctx.cfg.root / path
    if not p.exists():
        return []
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else data.get("agents", [])
    except json.JSONDecodeError:
        return []


@tool("catalog_lookup", "List the agents available for sale (name, one-line pitch, pricing card, status). Optional keyword filter.",
      {"keyword": "optional keyword to match against name/description/tags"})
def catalog_lookup(ctx, keyword: str | None = None) -> str:
    rows = load_catalog(ctx)
    if keyword:
        k = keyword.lower()
        rows = [r for r in rows if k in json.dumps(r, default=str).lower()]
    if not rows:
        return "catalog is empty" + (f" for '{keyword}'" if keyword else "")
    out = []
    for r in rows:
        price = r.get("pricing") or {}
        out.append(f"- {r.get('name')} [{r.get('slug')}] status={r.get('status')} v{r.get('version', '?')}: {r.get('description', '')[:160]}"
                   f" | price: {price.get('model')} {price.get('price')} per {price.get('unit')} | audience: {r.get('audience', '')[:80]}")
    return "\n".join(out)


@tool("quote_price", "Price an agent for a prospect using 2026 market benchmarks. Returns a quote with model, unit price, "
      "monthly estimate at the stated volume, and the rationale. Records the quote.",
      {"agent_slug": "catalog slug", "prospect": "company or person", "pricing_model": "per_seat | per_task | per_outcome | hybrid | custom_build",
       "volume": "seats per month, tasks per month, or outcomes per month (number)", "tier": "low | mid | high (default mid)"}, risk="write")
def quote_price(ctx, agent_slug: str, prospect: str, pricing_model: str = "hybrid", volume=100, tier: str = "mid") -> str:
    tier = tier if tier in ("low", "mid", "high") else "mid"
    try:
        vol = float(volume)
    except (TypeError, ValueError):
        vol = 100.0
    cat = {r.get("slug"): r for r in load_catalog(ctx)}
    agent = cat.get(agent_slug)
    if not agent:
        return f"ERROR: unknown agent '{agent_slug}'. Use catalog_lookup first."
    lines = []
    if pricing_model == "hybrid":
        base = PRICING_MODELS["per_seat"][tier] * 5  # 5-seat base
        task = PRICING_MODELS["per_task"][tier]
        monthly = base + task * vol
        lines.append({"component": "base subscription (5 seats)", "unit": "month", "price": base})
        lines.append({"component": "usage", "unit": "task", "price": task, "volume": vol, "subtotal": task * vol})
    elif pricing_model in PRICING_MODELS:
        m = PRICING_MODELS[pricing_model]
        price = m[tier]
        monthly = price if pricing_model == "custom_build" else price * vol
        lines.append({"component": pricing_model, "unit": m["unit"], "price": price, "volume": vol, "subtotal": monthly})
    else:
        return "ERROR: pricing_model must be per_seat | per_task | per_outcome | hybrid | custom_build"
    qid = f"q-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}-{re.sub(r'[^a-z0-9]+', '-', prospect.lower())[:24]}"
    quote = {"agent_slug": agent_slug, "agent_name": agent.get("name"), "prospect": prospect, "pricing_model": pricing_model, "tier": tier,
             "volume": vol, "lines": lines, "monthly_estimate": round(monthly, 2), "currency": "USD", "status": "draft",
             "rationale": f"{pricing_model} at {tier} tier of 2026 public benchmarks; hybrid = base + usage is the market direction "
                          f"(enterprise buyers want predictability, usage aligns cost to value).", "run_id": ctx.run_id}
    ctx.store.put("quotes", qid, quote)
    ctx.ledger.append("quote_created", ctx.run_id, quote_id=qid, prospect=prospect, agent=agent_slug, monthly=quote["monthly_estimate"])
    return f"quote {qid}: {json.dumps(quote, default=str)}"


@tool("draft_outreach", "Write an outreach email draft for a prospect and PROPOSE sending it. The draft is saved to the outbox and an "
      "approval 'send_email' is created; nothing is sent until the owner approves in Mission Control.",
      {"to": "recipient email or 'unknown'", "prospect": "company or person", "subject": "email subject", "body": "plain-text email body",
       "lead_id": "optional lead record id"}, risk="external", approval_action="send_email")
def draft_outreach(ctx, to: str, prospect: str, subject: str, body: str, lead_id: str | None = None) -> str:
    did = f"draft-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}-{re.sub(r'[^a-z0-9]+', '-', prospect.lower())[:24]}"
    draft = {"to": to, "prospect": prospect, "subject": subject[:200], "body": body[:4000], "lead_id": lead_id, "status": "awaiting_approval",
             "run_id": ctx.run_id}
    ctx.store.put("outbox", did, draft)
    outdir = ctx.cfg.data_dir / "outbox"
    outdir.mkdir(parents=True, exist_ok=True)
    (outdir / f"{did}.md").write_text(f"To: {to}\nSubject: {subject}\n\n{body}\n", encoding="utf-8")
    aid = ctx.store.create_approval(ctx.run_id, "send_email", did, f"Outreach to {prospect} ({to}): {subject[:80]}", {"draft_id": did, "to": to})
    ctx.proposed.append(aid)
    ctx.ledger.append("outreach_drafted", ctx.run_id, draft_id=did, prospect=prospect, approval_id=aid)
    return f"draft {did} saved; approval #{aid} 'send_email' is PENDING (owner must approve; not sent)"


@tool("propose_action", "Propose any approval-gated action for the owner (e.g. publish_agent, deploy_agent, schedule_call). Never executes.",
      {"action": "action name from the agent's [approvals].actions list", "target": "what it applies to", "rationale": "why"},
      risk="external")
def propose_action(ctx, action: str, target: str, rationale: str) -> str:
    if action not in ctx.cfg.approval_actions:
        return f"ERROR: '{action}' is not an approval action for this agent ({', '.join(ctx.cfg.approval_actions)})"
    aid = ctx.store.create_approval(ctx.run_id, action, target, rationale[:300])
    ctx.proposed.append(aid)
    ctx.ledger.append("action_proposed", ctx.run_id, action=action, target=target, approval_id=aid)
    return f"approval #{aid} '{action}' on {target} is PENDING"
