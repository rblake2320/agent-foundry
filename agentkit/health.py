"""Health report for one agent: runs, deliverables, tool error rates, model errors, injection flags, budget,
profiler signals (latency per tool / per model step from the ledger), doctor, ledger integrity, last evals and faults.
Grades green / amber / red with reasons. Read-only."""
from __future__ import annotations

from collections import defaultdict

from . import doctor
from .config import Config
from .ledger import Ledger
from .store import Store


def health_report(cfg: Config, runs_window: int = 20) -> dict:
    store, ledger = Store(cfg.db), Ledger(cfg.ledger)
    runs = store.list_runs(limit=runs_window)
    by_status = defaultdict(int)
    halts = defaultdict(int)
    durations, model_calls = [], []
    for r in runs:
        by_status[r["status"]] += 1
        if r["status"] in ("halted", "failed"):
            halts[(r.get("halt_reason") or "")[:80]] += 1
        rec = r.get("receipt") or {}
        if isinstance(rec, dict):
            if rec.get("duration_s") is not None:
                durations.append(float(rec["duration_s"]))
            if rec.get("model_calls") is not None:
                model_calls.append(int(rec["model_calls"]))
    run_ids = {r["id"] for r in runs}
    events = [e for e in ledger.read(limit=5000) if e.get("run_id") in run_ids]
    tool_total, tool_err, tool_lat = defaultdict(int), defaultdict(int), defaultdict(list)
    step_lat = []
    unmet, verified_false, injections, model_errors = defaultdict(int), 0, 0, 0
    for e in events:
        d = e.get("detail") or {}
        if e["event"] == "tool_call":
            tool_total[d.get("tool")] += 1
            if not d.get("ok", True):
                tool_err[d.get("tool")] += 1
            if d.get("latency_s") is not None:
                tool_lat[d.get("tool")].append(float(d["latency_s"]))
        elif e["event"] == "model_step" and d.get("latency_s") is not None:
            step_lat.append(float(d["latency_s"]))
        elif e["event"] == "task_finished":
            if not d.get("verified", True):
                verified_false += 1
            for m in d.get("missing") or []:
                unmet[str(m)[:100]] += 1
        elif e["event"] == "injection_flagged":
            injections += 1
        elif e["event"] == "model_error":
            model_errors += 1
    tools = {t: {"calls": n, "errors": tool_err[t], "error_rate": round(tool_err[t] / n, 3),
                 "avg_latency_s": round(sum(tool_lat[t]) / len(tool_lat[t]), 2) if tool_lat[t] else None} for t, n in tool_total.items()}
    budget = store.month_budget()
    doc = doctor.summarize(doctor.run_checks(cfg))
    lv = ledger.verify()
    evals = store.list("evals", limit=1)
    faults = store.get("faults", "latest")
    reasons, grade = [], "green"

    def worse(level, why):
        nonlocal grade
        reasons.append(f"[{level}] {why}")
        if level == "red" or (level == "amber" and grade == "green"):
            grade = level if not (level == "amber" and grade == "red") else grade

    if not doc["ok"]:
        worse("red", f"doctor: {doc['failed_required']} required check(s) failing")
    if not lv["ok"]:
        worse("red", f"ledger chain broken at line {lv['first_bad_line']}")
    if by_status.get("failed", 0):
        worse("red", f"{by_status['failed']} failed run(s) in the last {len(runs)}")
    if runs and by_status.get("halted", 0) / len(runs) > 0.5:
        worse("amber", f"{by_status['halted']} of {len(runs)} runs halted: " + "; ".join(f"{k} ×{v}" for k, v in list(halts.items())[:3]))
    bad_tools = [t for t, s in tools.items() if s["calls"] >= 3 and s["error_rate"] > 0.3]
    if bad_tools:
        worse("amber", f"tool error rate > 30%: {', '.join(bad_tools)}")
    if verified_false:
        worse("amber", f"{verified_false} task(s) finished with unmet deliverable items: " + "; ".join(list(unmet)[:3]))
    if model_errors:
        worse("amber", f"{model_errors} model error(s)")
    if budget["model_calls"] >= 0.8 * cfg.limits.monthly_model_call_cap:
        worse("amber", f"monthly model budget {budget['model_calls']}/{cfg.limits.monthly_model_call_cap}")
    if faults and faults.get("verdict") == "FAIL":
        worse("red", f"fault injection: {faults['passed']}/{faults['total']} scenarios contained")
    if evals and evals[0].get("verdict") == "FAIL":
        worse("amber", f"last evals: quality {evals[0].get('avg_quality')} safety {evals[0].get('avg_safety')}")
    if not runs:
        worse("amber", "no runs yet")
    return {
        "agent": cfg.agent.slug, "grade": grade, "reasons": reasons,
        "runs": {"window": len(runs), "by_status": dict(by_status), "halt_reasons": dict(halts),
                 "avg_duration_s": round(sum(durations) / len(durations), 1) if durations else None,
                 "avg_model_calls": round(sum(model_calls) / len(model_calls), 1) if model_calls else None},
        "deliverables": {"tasks_with_unmet_items": verified_false, "unmet_items": dict(unmet)},
        "tools": tools, "profiler": {"model_steps": len(step_lat), "avg_step_latency_s": round(sum(step_lat) / len(step_lat), 2) if step_lat else None,
                                     "p95_step_latency_s": round(sorted(step_lat)[int(0.95 * (len(step_lat) - 1))], 2) if step_lat else None,
                                     "slowest_tools": sorted(((t, s["avg_latency_s"]) for t, s in tools.items() if s["avg_latency_s"]), key=lambda x: -x[1])[:3]},
        "safety": {"injection_flags": injections, "model_errors": model_errors},
        "budget": {**budget, "cap": cfg.limits.monthly_model_call_cap},
        "doctor": {"ok": doc["ok"], "failed_required": doc["failed_required"], "warnings": doc["warnings"]},
        "ledger": lv, "last_eval": evals[0] if evals else None, "last_faults": {k: v for k, v in (faults or {}).items() if k != "results"} or None,
    }
