"""Receipted Markdown report for one run: summary, per-task outcomes, proposed actions, uncertainty, receipt."""
from __future__ import annotations

import shutil
from datetime import date
from pathlib import Path

from .config import Config


def write_report(cfg: Config, run_id: str, status: str, halt_reason: str, receipt: dict, task_results: list[dict],
                 approvals: list[dict], errors: list[str], summary: str | None = None) -> Path:
    cfg.reports_dir.mkdir(parents=True, exist_ok=True)
    path = cfg.reports_dir / f"{cfg.agent.slug}-report-{date.today().isoformat()}-{run_id}.md"
    L: list[str] = [f"# {cfg.agent.name} — run report — {date.today().isoformat()} — run {run_id}", "",
                    f"Status: **{status}** · Halt reason: {halt_reason}", "", "## EXECUTIVE SUMMARY", ""]
    L.append(summary or f"{len(task_results)} task(s) ran; {sum(1 for t in task_results if t.get('status') == 'done')} completed the deliverable checklist.")
    L += ["", "## TASK OUTCOMES", ""]
    for t in task_results:
        L.append(f"### {t['task']} — {t.get('status')}")
        L.append(f"- steps: {t.get('steps')} · tool calls: {t.get('tool_calls')} · model calls: {t.get('model_calls')} · verified: {t.get('verified')}")
        if t.get("missing"):
            L.append(f"- deliverable items not met: {', '.join(t['missing'])}")
        L.append("")
        L.append(t.get("final") or "_(no final output)_")
        L.append("")
    L += ["## PROPOSED ACTIONS (pending approval — nothing executed)", ""]
    L += [f"- #{a['id']} `{a['action']}` → **{a['target']}** — {a.get('rationale', '')}" for a in approvals] or ["None."]
    L += ["", "## UNCERTAINTY / LIMITATIONS", ""]
    L += [f"- {e}" for e in errors[:30]] or ["- none recorded"]
    L += ["", "## RECEIPT", ""]
    for k, v in receipt.items():
        if k not in ("limits",):
            L.append(f"- {k}: {v}")
    L.append(f"- limits: {receipt.get('limits')}")
    L.append("")
    path.write_text("\n".join(L) + "\n", encoding="utf-8")
    try:
        cfg.inbox.mkdir(parents=True, exist_ok=True)
        if cfg.inbox.resolve() != cfg.reports_dir.resolve():
            shutil.copy2(path, cfg.inbox / path.name)
    except OSError:
        pass
    return path
