"""Five-gate verification of a built product: doctor, tests, smoke run, ledger, agent card."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from agentkit import brain, config, doctor
from agentkit.ledger import Ledger
from agentkit.mc import agent_card
from agentkit.store import Store
from agentkit.worker import Worker


def verify_product(product_dir: Path, kit_root: Path, smoke: bool = True, smoke_input: str = "") -> dict:
    product_dir = Path(product_dir)
    gates: dict[str, dict] = {}
    cfg = config.load(product_dir)

    d = doctor.summarize(doctor.run_checks(cfg))
    gates["doctor"] = {"ok": d["ok"], "evidence": [c["name"] + ": " + c["detail"][:80] for c in d["checks"] if not c["ok"]] or ["all required checks pass"]}

    p = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", str(product_dir / "tests")], capture_output=True, text=True,
                       cwd=str(kit_root), timeout=600, encoding="utf-8", errors="replace")
    summary = [ln for ln in (p.stdout + p.stderr).splitlines() if "passed" in ln or "failed" in ln or "error" in ln.lower()][-1:] or [(p.stdout + p.stderr)[-200:]]
    gates["tests"] = {"ok": p.returncode == 0, "evidence": summary}

    if smoke and gates["doctor"]["ok"] and gates["tests"]["ok"]:
        tasks = brain.list_tasks(cfg)
        first = next((t for t in tasks if t["schedule"] != "manual"), tasks[0] if tasks else None)
        if first is None:
            gates["smoke"] = {"ok": False, "evidence": ["no task to run"]}
        else:
            w = Worker(cfg, Store(cfg.db), Ledger(cfg.ledger))
            res = w.run(first["name"], smoke_input)
            rec = res.get("receipt") or {}
            report_ok = bool(res.get("report_path")) and Path(res["report_path"]).exists()
            final_nonempty = False
            if report_ok:
                text = Path(res["report_path"]).read_text(encoding="utf-8")
                final_nonempty = "_(no final output)_" not in text
            ok = res.get("status") == "completed" and rec.get("tasks_done", 0) >= 1 and report_ok and final_nonempty
            gates["smoke"] = {"ok": ok, "evidence": [f"run {res.get('id')} status={res.get('status')} halt={res.get('halt_reason')}",
                                                     f"task={first['name']} tasks_done={rec.get('tasks_done')} tool_calls={rec.get('tool_calls')} model_calls={rec.get('model_calls')} duration={rec.get('duration_s')}s",
                                                     f"report={res.get('report_path')}"], "run_id": res.get("id")}
    else:
        gates["smoke"] = {"ok": False, "evidence": ["skipped: earlier gate failed" if smoke else "skipped by caller"]}

    v = Ledger(cfg.ledger).verify()
    gates["ledger"] = {"ok": v["ok"], "evidence": [json.dumps(v)]}

    card = agent_card(cfg)
    required = ("name", "description", "supportedInterfaces", "version", "capabilities", "defaultInputModes", "defaultOutputModes", "skills")
    missing = [k for k in required if not card.get(k)]
    gates["card"] = {"ok": not missing, "evidence": [f"skills={len(card.get('skills', []))}", f"missing={missing}" if missing else "all required fields present"]}

    verdict = "PASS" if all(g["ok"] for g in gates.values()) else "FAIL"
    first_fail = next((n for n, g in gates.items() if not g["ok"]), None)
    return {"verdict": verdict, "first_failing_gate": first_fail, "gates": gates, "product": str(product_dir), "slug": cfg.agent.slug}
