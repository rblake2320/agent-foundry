"""Agent spec: the contract between a commission and the generator. Validated against the runtime."""
from __future__ import annotations

import re

from agentkit.tools import REGISTRY

APPROVAL_ACTIONS = ["send_email", "publish_agent", "deploy_agent", "launch_agent", "schedule_call"]
PRICING_MODELS = ["per_seat", "per_task", "per_outcome", "hybrid", "custom_build"]
SCHEDULES = ["daily", "weekly", "manual"]

SPEC_TEMPLATE = {
    "name": "Human readable name", "slug": "kebab-case-slug", "version": "1.0.0",
    "description": "one sentence", "responsibility": "verb + outcome sentence", "audience": "who pays and why",
    "trigger": {"schedule": "daily", "time": "07:30"},
    "tools": ["web_search", "record_put"],
    "approvals": ["send_email"],
    "limits": {"max_steps_per_task": 10, "max_tool_calls_per_task": 16, "max_model_calls_per_run": 60, "max_run_minutes": 45},
    "soul": {"tone": "terse, evidence-first", "values": ["...", "..."], "boundaries": ["...", "..."]},
    "user_context": ["fact about the owner/business the agent needs", "..."],
    "skills": [{"name": "kebab-name", "category": "sales", "description": "when to load it", "when_to_use": ["..."], "quick_reference": ["..."],
                "procedure": ["step", "step"], "pitfalls": ["..."], "verification": ["..."]}],
    "tasks": [{"name": "kebab-name", "schedule": "daily", "skills": ["kebab-name"], "tools": ["web_search"], "description": "one line",
               "instructions": "what to do, in prose", "deliverable": ["checklist item", "checklist item", "checklist item"]}],
    "pricing": {"model": "hybrid", "price": 249.0, "unit": "month + $0.20/task", "rationale": "..."},
    "panels": [{"name": "Leads", "collection": "leads", "columns": ["company", "status", "score"]}],
    "assumptions": ["..."],
}


def validate(spec: dict) -> list[str]:
    errs: list[str] = []
    for k in ("name", "slug", "description", "responsibility", "audience", "tools", "tasks", "skills", "pricing"):
        if k not in spec or spec[k] in (None, "", [], {}):
            errs.append(f"missing {k}")
    slug = str(spec.get("slug", ""))
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]{1,40}", slug):
        errs.append(f"slug must be kebab-case: {slug!r}")
    tools = spec.get("tools") or []
    bad = [t for t in tools if t not in REGISTRY]
    if bad:
        errs.append(f"unknown tools {bad}; registry has {sorted(REGISTRY)}")
    for a in spec.get("approvals") or []:
        if a not in APPROVAL_ACTIONS:
            errs.append(f"unknown approval action {a}; allowed {APPROVAL_ACTIONS}")
    for t in REGISTRY.values():
        if t.name in tools and t.approval_action and t.approval_action not in (spec.get("approvals") or []):
            errs.append(f"tool {t.name} requires approval action {t.approval_action} in approvals")
    names = set()
    for i, task in enumerate(spec.get("tasks") or []):
        n = str(task.get("name", ""))
        if not re.fullmatch(r"[a-z0-9][a-z0-9-]{1,40}", n):
            errs.append(f"task {i} name must be kebab-case: {n!r}")
        if n in names:
            errs.append(f"duplicate task name {n}")
        names.add(n)
        if task.get("schedule", "manual") not in SCHEDULES:
            errs.append(f"task {n}: schedule must be one of {SCHEDULES}")
        extra = [x for x in task.get("tools") or [] if x not in tools]
        if extra:
            errs.append(f"task {n}: tools {extra} not in the agent's tool list")
        if len(task.get("deliverable") or []) < 2:
            errs.append(f"task {n}: deliverable checklist needs at least 2 items")
        if not task.get("instructions"):
            errs.append(f"task {n}: instructions missing")
    skill_names = {s.get("name") for s in spec.get("skills") or []}
    for task in spec.get("tasks") or []:
        for s in task.get("skills") or []:
            if s not in skill_names:
                errs.append(f"task {task.get('name')}: skill {s} not defined in spec.skills")
    for s in spec.get("skills") or []:
        if not re.fullmatch(r"[a-z0-9][a-z0-9-]{1,40}", str(s.get("name", ""))):
            errs.append(f"skill name must be kebab-case: {s.get('name')!r}")
        for k in ("procedure", "verification"):
            if len(s.get(k) or []) < 2:
                errs.append(f"skill {s.get('name')}: {k} needs at least 2 items")
    p = spec.get("pricing") or {}
    if p.get("model") not in PRICING_MODELS:
        errs.append(f"pricing.model must be one of {PRICING_MODELS}")
    try:
        float(p.get("price", "x"))
    except (TypeError, ValueError):
        errs.append("pricing.price must be a number")
    lim = spec.get("limits") or {}
    for k, cap in (("max_steps_per_task", 20), ("max_tool_calls_per_task", 40), ("max_model_calls_per_run", 200), ("max_run_minutes", 180)):
        v = lim.get(k)
        if v is not None and (not isinstance(v, int) or v < 1 or v > cap):
            errs.append(f"limits.{k} must be an int in 1..{cap}")
    return errs


def normalize(spec: dict) -> dict:
    spec = dict(spec)
    spec.setdefault("version", "1.0.0")
    spec.setdefault("approvals", [])
    spec.setdefault("limits", {})
    spec.setdefault("trigger", {"schedule": "daily", "time": "07:30"})
    spec.setdefault("soul", {"tone": "terse, evidence-first", "values": [], "boundaries": []})
    spec.setdefault("user_context", [])
    spec.setdefault("panels", [])
    spec.setdefault("assumptions", [])
    for t in spec.get("tasks") or []:
        t.setdefault("schedule", "manual")
        t.setdefault("skills", [])
        t.setdefault("tools", [])
        t.setdefault("description", "")
    for s in spec.get("skills") or []:
        s.setdefault("category", "general")
        for k in ("when_to_use", "quick_reference", "pitfalls"):
            s.setdefault(k, [])
    return spec
