"""The trigger: Windows Task Scheduler on Windows, crontab on Linux/macOS. One task per agent slug."""
from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

from .config import Config


def is_windows() -> bool:
    return os.name == "nt"


def task_name(cfg: Config) -> str:
    return f"AgentFoundry-{cfg.agent.slug}"


def cron_tag(cfg: Config) -> str:
    return f"# agentfoundry-{cfg.agent.slug}"


def wrapper_path(cfg: Config) -> Path:
    return cfg.root / "scripts" / ("run_agent.cmd" if is_windows() else "run_agent.sh")


def write_wrapper(cfg: Config) -> Path:
    w = wrapper_path(cfg)
    w.parent.mkdir(parents=True, exist_ok=True)
    log = cfg.data_dir / "scheduled.log"
    kit_root = Path(__file__).resolve().parent.parent
    if is_windows():
        w.write_text(f"@echo off\r\ncd /d \"{kit_root}\"\r\n\"{sys.executable}\" -m agentkit --root \"{cfg.root}\" run >> \"{log}\" 2>&1\r\n", encoding="utf-8")
    else:
        w.write_text(f"#!/bin/sh\ncd \"{kit_root}\" || exit 1\n\"{sys.executable}\" -m agentkit --root \"{cfg.root}\" run >> \"{log}\" 2>&1\n", encoding="utf-8")
        w.chmod(0o755)
    return w


def _schtasks(*args: str) -> tuple[int, str]:
    p = subprocess.run(["schtasks", *args], capture_output=True, text=True, encoding="utf-8", errors="replace")
    return p.returncode, (p.stdout + p.stderr).strip()


# ---- cron helpers (pure, testable anywhere)
def cron_line(wrapper: Path, time_hhmm: str, tag: str) -> str:
    hh, mm = time_hhmm.split(":")
    return f"{int(mm)} {int(hh)} * * * {wrapper} {tag}"


def cron_add(existing: str, line: str, tag: str) -> str:
    kept = [ln for ln in existing.splitlines() if tag not in ln]
    kept.append(line)
    return "\n".join(kept).strip("\n") + "\n"


def cron_remove(existing: str, tag: str) -> str:
    kept = [ln for ln in existing.splitlines() if tag not in ln]
    return ("\n".join(kept).strip("\n") + "\n") if kept else ""


def cron_parse(existing: str, tag: str) -> dict | None:
    for ln in existing.splitlines():
        if tag in ln and not ln.lstrip().startswith("#"):
            m = re.match(r"^\s*(\S+)\s+(\S+)\s+(\S+)\s+(\S+)\s+(\S+)\s+(.*?)\s*" + re.escape(tag), ln)
            if m:
                return {"minute": m.group(1), "hour": m.group(2), "command": m.group(6)}
    return None


def _crontab_read() -> str:
    p = subprocess.run(["crontab", "-l"], capture_output=True, text=True)
    return p.stdout if p.returncode == 0 else ""


def _crontab_write(content: str) -> tuple[int, str]:
    p = subprocess.run(["crontab", "-"], input=content, capture_output=True, text=True)
    return p.returncode, (p.stdout + p.stderr).strip()


# ---- public API
def install(cfg: Config, time_hhmm: str | None = None) -> dict:
    t = time_hhmm or cfg.schedule_time
    if not re.fullmatch(r"\d{1,2}:\d{2}", t):
        return {"ok": False, "output": f"bad time {t!r}; use HH:MM"}
    w = write_wrapper(cfg)
    if is_windows():
        rc, out = _schtasks("/Create", "/F", "/TN", task_name(cfg), "/SC", "DAILY", "/ST", t, "/TR", f"\"{w}\"")
        return {"ok": rc == 0, "output": out, "task": task_name(cfg), "time": t, "wrapper": str(w), "backend": "schtasks"}
    line = cron_line(w, t, cron_tag(cfg))
    rc, out = _crontab_write(cron_add(_crontab_read(), line, cron_tag(cfg)))
    return {"ok": rc == 0, "output": out or line, "task": cron_tag(cfg), "time": t, "wrapper": str(w), "backend": "crontab"}


def remove(cfg: Config) -> dict:
    if is_windows():
        rc, out = _schtasks("/Delete", "/F", "/TN", task_name(cfg))
        return {"ok": rc == 0, "output": out, "backend": "schtasks"}
    rc, out = _crontab_write(cron_remove(_crontab_read(), cron_tag(cfg)))
    return {"ok": rc == 0, "output": out, "backend": "crontab"}


def run_now(cfg: Config) -> dict:
    if is_windows():
        rc, out = _schtasks("/Run", "/TN", task_name(cfg))
        return {"ok": rc == 0, "output": out, "backend": "schtasks"}
    w = wrapper_path(cfg)
    if not w.exists():
        return {"ok": False, "output": "wrapper missing; install first", "backend": "crontab"}
    subprocess.Popen(["/bin/sh", str(w)], start_new_session=True)
    return {"ok": True, "output": f"started {w}", "backend": "crontab"}


def status(cfg: Config) -> dict:
    if is_windows():
        rc, out = _schtasks("/Query", "/TN", task_name(cfg), "/FO", "LIST", "/V")
        if rc != 0:
            return {"installed": False, "task": task_name(cfg), "backend": "schtasks"}
        info = {}
        for line in out.splitlines():
            if ":" in line:
                k, v = line.split(":", 1)
                info[k.strip()] = v.strip()
        return {"installed": True, "task": task_name(cfg), "backend": "schtasks", "status": info.get("Status") or info.get("Scheduled Task State"),
                "next_run": info.get("Next Run Time"), "last_run": info.get("Last Run Time"), "last_result": info.get("Last Result"),
                "schedule": info.get("Schedule Type"), "start_time": info.get("Start Time"), "task_to_run": info.get("Task To Run")}
    e = cron_parse(_crontab_read(), cron_tag(cfg))
    if not e:
        return {"installed": False, "task": cron_tag(cfg), "backend": "crontab"}
    return {"installed": True, "task": cron_tag(cfg), "backend": "crontab", "status": "Ready", "schedule": "Daily",
            "start_time": f"{int(e['hour']):02d}:{int(e['minute']):02d}", "task_to_run": e["command"], "next_run": None, "last_run": None, "last_result": None}
