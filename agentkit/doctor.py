"""Doctor: prove every prerequisite for an agent before it runs. Same checks in CLI and Mission Control."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import urllib.request

from . import brain
from .config import Config
from .tools import REGISTRY


def run_checks(cfg: Config) -> list[dict]:
    checks: list[dict] = []

    def add(name, ok, detail, required=True, fix=""):
        checks.append({"name": name, "ok": bool(ok), "detail": str(detail)[:300], "required": required, "fix": fix})

    v = sys.version_info
    add("python >= 3.11", v >= (3, 11), f"{sys.executable} {v.major}.{v.minor}.{v.micro}", fix="install Python 3.11+")
    try:
        import fastapi, uvicorn  # noqa: F401
        add("fastapi + uvicorn importable", True, f"fastapi {fastapi.__version__}", fix="pip install -r requirements.txt")
    except Exception as e:  # noqa: BLE001
        add("fastapi + uvicorn importable", False, str(e), fix="pip install -r requirements.txt")
    add("agent.toml loaded", True, f"{cfg.agent.name} v{cfg.agent.version} ({cfg.agent.slug})")
    missing = [n for n, p in cfg.core_files.items() if not p.exists()]
    add("core files SOUL/AGENTS/USER/MEMORY present", not missing, ", ".join(missing) or "all four present", fix="restore the missing core .md files")
    skills = brain.list_skills(cfg)
    add("at least one skill", bool(skills), ", ".join(s["name"] for s in skills) or "none", fix="add skills/<category>/<name>/SKILL.md")
    tasks = brain.list_tasks(cfg)
    add("at least one task", bool(tasks), ", ".join(t["name"] for t in tasks) or "none", fix="add tasks/<name>.md with a ## Deliverable list")
    bad_tools = [t for t in cfg.tools_allowed if t not in REGISTRY]
    add("allowed tools exist in the registry", not bad_tools, ", ".join(cfg.tools_allowed) or "no tools", fix=f"unknown tools: {bad_tools}")
    for t in tasks:
        extra = [x for x in t.get("tools", []) if x not in cfg.tools_allowed]
        if extra:
            add(f"task '{t['name']}' tools are allowlisted", False, f"not allowed: {extra}", fix="add them to [tools].allowed or remove from the task")
    if cfg.model.backend == "ollama":
        try:
            with urllib.request.urlopen(cfg.model.ollama_url.rstrip("/") + "/api/tags", timeout=5) as r:
                names = [m["name"] for m in json.load(r).get("models", [])]
            add("ollama reachable", True, f"{len(names)} models", fix="start ollama")
            add(f"model '{cfg.model.ollama_model}' pulled", cfg.model.ollama_model in names, ", ".join(names[:5]),
                fix=f"ollama pull {cfg.model.ollama_model} (or set [model].backend = \"none\")")
        except Exception as e:  # noqa: BLE001
            add("ollama reachable", False, str(e)[:120], fix="start ollama, or set [model].backend to \"claude\" or \"none\"")
    elif cfg.model.backend == "claude":
        try:
            p = subprocess.run(["claude", "--version"], capture_output=True, text=True, timeout=20)
            add("claude CLI on PATH", p.returncode == 0, p.stdout.strip(), fix="install Claude Code or switch backend")
        except (OSError, subprocess.TimeoutExpired) as e:
            add("claude CLI on PATH", False, str(e), fix="install Claude Code or switch backend")
    else:
        add("model backend", True, "none: tasks run without a model are refused with a clear reason", required=False)
    if "web_search" in cfg.tools_allowed:
        try:
            import ddgs  # noqa: F401
            add("web search library (ddgs)", True, "ddgs importable", required=False, fix="pip install ddgs (HTML fallback is used otherwise)")
        except Exception:  # noqa: BLE001
            add("web search library (ddgs)", True, "ddgs missing; HTML fallback active", required=False, fix="pip install ddgs")
    for label, p in (("data dir", cfg.data_dir), ("reports dir", cfg.reports_dir), ("inbox", cfg.inbox)):
        try:
            p.mkdir(parents=True, exist_ok=True)
            probe = p / ".doctor_probe"
            probe.write_text("ok", encoding="utf-8")
            probe.unlink()
            add(f"writable {label}", True, str(p))
        except OSError as e:
            add(f"writable {label}", False, f"{p}: {e}", fix="fix permissions or change [paths]")
    add("scheduler available", shutil.which("schtasks" if os.name == "nt" else "crontab") is not None,
        "schtasks" if os.name == "nt" else "crontab", required=False, fix="optional; needed only for `schedule install`")
    add("mission control port configured", 1024 < cfg.mc_port < 65536, f"{cfg.mc_host}:{cfg.mc_port}", fix="set [mission_control].port")
    return checks


def summarize(checks: list[dict]) -> dict:
    failed = [c for c in checks if not c["ok"] and c["required"]]
    warns = [c for c in checks if not c["ok"] and not c["required"]]
    return {"ok": not failed, "checks": checks, "failed_required": len(failed), "warnings": len(warns)}


def format_report(s: dict) -> str:
    lines = [f"[{'PASS' if c['ok'] else ('FAIL' if c['required'] else 'WARN')}] {c['name']}: {c['detail']}" + (f"  -> {c['fix']}" if not c['ok'] and c['fix'] else "")
             for c in s["checks"]]
    lines.append("")
    lines.append("READY: all required checks pass" if s["ok"] else f"NOT READY: {s['failed_required']} required check(s) failed")
    return "\n".join(lines)
