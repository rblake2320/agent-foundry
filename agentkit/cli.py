"""Generic CLI for any agentkit agent:  python -m agentkit --root <agent folder> <command>"""
from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from pathlib import Path

from . import approvals, brain, config, doctor, schedule
from .ledger import Ledger
from .model import BudgetExceeded, ModelClient, ModelError
from .store import Store
from .worker import Worker


def load_agent(root: Path):
    """Return (cfg, WorkerClass, panels). A package may ship agent.py defining `Worker` and `PANELS`; it is imported as a module."""
    cfg = config.load(root)
    worker_cls, panels = Worker, []
    entry = root / "agent.py"
    if entry.exists():
        spec = importlib.util.spec_from_file_location(f"agent_{cfg.agent.slug.replace('-', '_')}", entry)
        mod = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = mod
        spec.loader.exec_module(mod)  # type: ignore[union-attr]
        worker_cls = getattr(mod, "Worker", Worker)
        panels = getattr(mod, "PANELS", [])
    return cfg, worker_cls, panels


def main(argv: list[str] | None = None) -> int:
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    ap = argparse.ArgumentParser(prog="agentkit")
    ap.add_argument("--root", default=".", help="agent folder containing agent.toml")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="run all scheduled tasks, or one task")
    r.add_argument("task", nargs="?")
    r.add_argument("--input", default="", help="extra input text for the task")
    sub.add_parser("tasks", help="list tasks")
    sub.add_parser("skills", help="list skills")
    sub.add_parser("doctor", help="check prerequisites")
    sub.add_parser("status", help="last run, pending approvals, budget")
    sub.add_parser("verify-log", help="verify the hash-chained ledger")
    sub.add_parser("card", help="print the A2A agent card")
    a = sub.add_parser("approvals", help="list/approve/deny/execute")
    a.add_argument("action", choices=["list", "approve", "deny", "execute"])
    a.add_argument("id", nargs="?", type=int)
    a.add_argument("--dry-run", action="store_true")
    s = sub.add_parser("schedule", help="trigger: install/remove/status/run")
    s.add_argument("action", choices=["install", "remove", "status", "run"])
    s.add_argument("--time")
    m = sub.add_parser("mc", help="start Mission Control")
    m.add_argument("--port", type=int)
    c = sub.add_parser("chat", help="ask the agent about its state (read-only)")
    c.add_argument("question")
    args = ap.parse_args(argv)

    root = Path(args.root).resolve()
    cfg, worker_cls, panels = load_agent(root)
    store, ledger = Store(cfg.db), Ledger(cfg.ledger)

    if args.cmd == "run":
        res = worker_cls(cfg, store, ledger).run(args.task, args.input)
        print(json.dumps({k: res.get(k) for k in ("id", "status", "halt_reason", "report_path")}, indent=2))
        rec = res.get("receipt") or {}
        print(json.dumps({k: rec.get(k) for k in ("tasks", "tasks_done", "tool_calls", "model_calls", "duration_s")}, indent=2, default=str))
        return 0 if res.get("status") in ("completed", "halted") else 1
    if args.cmd == "tasks":
        for t in brain.list_tasks(cfg):
            print(f"{t['name']:<28} schedule={t['schedule']:<10} tools={t['tools']} deliverable={len(t['deliverable'])} items")
        return 0
    if args.cmd == "skills":
        for s_ in brain.list_skills(cfg):
            print(f"{s_['name']:<28} [{s_['category']}] {s_['description'][:90]}")
        return 0
    if args.cmd == "doctor":
        summ = doctor.summarize(doctor.run_checks(cfg))
        print(doctor.format_report(summ))
        return 0 if summ["ok"] else 1
    if args.cmd == "status":
        last = store.list_runs(limit=1)
        print(json.dumps({"agent": cfg.agent.slug, "last_run": last[0] if last else None, "pending_approvals": len(store.list_approvals("pending")),
                          "budget": store.month_budget()}, indent=2, default=str))
        return 0
    if args.cmd == "verify-log":
        v = ledger.verify()
        print(json.dumps(v))
        return 0 if v["ok"] else 1
    if args.cmd == "card":
        from .mc import agent_card
        print(json.dumps(agent_card(cfg), indent=2))
        return 0
    if args.cmd == "approvals":
        if args.action == "list":
            for ap_ in store.list_approvals():
                print(f"#{ap_['id']:<4} {ap_['status']:<9} {ap_['action']:<16} {ap_['target']}  {ap_.get('rationale', '')}")
            return 0
        if args.id is None:
            ap.error("approval id required")
        if args.action in ("approve", "deny"):
            print(json.dumps(approvals.decide(store, ledger, args.id, args.action == "approve", who="cli"), default=str, indent=2))
            return 0
        res = approvals.execute(cfg, store, ledger, args.id, dry_run=args.dry_run)
        print(json.dumps(res, default=str, indent=2))
        return 0 if res.get("ok", True) else 1
    if args.cmd == "schedule":
        fn = {"install": lambda: schedule.install(cfg, args.time), "remove": lambda: schedule.remove(cfg),
              "status": lambda: schedule.status(cfg), "run": lambda: schedule.run_now(cfg)}[args.action]
        print(json.dumps(fn(), indent=2))
        return 0
    if args.cmd == "mc":
        import uvicorn
        from .mc import create_app
        uvicorn.run(create_app(cfg, worker_cls, panels), host=cfg.mc_host, port=args.port or cfg.mc_port, log_level="info")
        return 0
    if args.cmd == "chat":
        from .mc import chat_answer
        try:
            print(chat_answer(cfg, store, ModelClient(cfg, store), args.question))
        except (ModelError, BudgetExceeded) as e:
            print(f"error: {e}")
            return 1
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
