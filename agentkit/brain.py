"""The brain: core files, skills (agentskills.io-compatible SKILL.md), tasks (tasks/*.md), durable memory, daily notes."""
from __future__ import annotations

import re
from datetime import date, datetime, timezone
from pathlib import Path

from .config import Config

_LESSONS = "## Lessons"
_DECISIONS = "## Owner decisions"


def _frontmatter(text: str) -> tuple[dict, str]:
    m = re.match(r"^---\s*\n(.*?)\n---\s*\n?(.*)$", text, flags=re.S)
    if not m:
        return {}, text
    fm: dict = {}
    for line in m.group(1).splitlines():
        if ":" in line:
            k, v = line.split(":", 1)
            v = v.strip()
            if v.startswith("[") and v.endswith("]"):
                fm[k.strip()] = [x.strip().strip("'\"") for x in v[1:-1].split(",") if x.strip()]
            else:
                fm[k.strip()] = v.strip("'\"")
    return fm, m.group(2)


def read_core(cfg: Config) -> dict[str, str]:
    return {n: (p.read_text(encoding="utf-8") if p.exists() else "") for n, p in cfg.core_files.items()}


def list_skills(cfg: Config) -> list[dict]:
    out = []
    for p in sorted(cfg.skills_dir.rglob("SKILL.md")) if cfg.skills_dir.exists() else []:
        fm, body = _frontmatter(p.read_text(encoding="utf-8"))
        out.append({"name": fm.get("name", p.parent.name), "description": fm.get("description", ""),
                    "category": p.parent.parent.name, "path": str(p.relative_to(cfg.root)), "chars": len(body)})
    return out


def read_skill(cfg: Config, name: str) -> str:
    for p in cfg.skills_dir.rglob("SKILL.md") if cfg.skills_dir.exists() else []:
        fm, _ = _frontmatter(p.read_text(encoding="utf-8"))
        if fm.get("name", p.parent.name) == name:
            return p.read_text(encoding="utf-8")
    return ""


def list_tasks(cfg: Config) -> list[dict]:
    out = []
    for p in sorted(cfg.tasks_dir.glob("*.md")) if cfg.tasks_dir.exists() else []:
        fm, body = _frontmatter(p.read_text(encoding="utf-8"))
        out.append({"name": fm.get("name", p.stem), "file": p.name, "schedule": fm.get("schedule", "manual"),
                    "skills": fm.get("skills", []), "tools": fm.get("tools", []), "description": fm.get("description", ""),
                    "deliverable": _deliverable(body), "body": body})
    return out


def read_task(cfg: Config, name: str) -> dict | None:
    for t in list_tasks(cfg):
        if t["name"] == name or t["file"] == name or t["file"] == f"{name}.md":
            return t
    return None


def _deliverable(body: str) -> list[str]:
    m = re.search(r"## Deliverable\s*\n(.*?)(?:\n## |\Z)", body, flags=re.S)
    if not m:
        return []
    return [ln.strip()[2:].strip() for ln in m.group(1).splitlines() if ln.strip().startswith("- ")]


def remember_lesson(cfg: Config, lesson: str, cap: int = 40) -> None:
    p = cfg.core_files["MEMORY"]
    text = p.read_text(encoding="utf-8") if p.exists() else f"# MEMORY.md\n\n{_LESSONS}\n\n{_DECISIONS}\n"
    entry = f"- {datetime.now(timezone.utc).strftime('%Y-%m-%d')}: {lesson.strip()[:240]}"
    if _LESSONS in text:
        head, rest = text.split(_LESSONS, 1)
        tail_marker = _DECISIONS if _DECISIONS in rest else None
        lessons, tail = (rest.split(_DECISIONS, 1) if tail_marker else (rest, ""))
        items = [ln for ln in lessons.splitlines() if ln.strip().startswith("- ")]
        if entry in items:
            return
        items = (items + [entry])[-cap:]
        text = head + _LESSONS + "\n" + "\n".join(items) + "\n" + (("\n" + _DECISIONS + tail) if tail_marker else "\n")
    else:
        text = text.rstrip("\n") + f"\n\n{_LESSONS}\n{entry}\n"
    p.write_text(text, encoding="utf-8")


def remember_decision(cfg: Config, decision: str) -> None:
    p = cfg.core_files["MEMORY"]
    text = p.read_text(encoding="utf-8") if p.exists() else "# MEMORY.md\n"
    entry = f"- {date.today().isoformat()}: {decision.strip()[:240]}"
    if entry in text:
        return
    if _DECISIONS in text:
        text = text.rstrip("\n") + "\n" + entry + "\n"
    else:
        text = text.rstrip("\n") + f"\n\n{_DECISIONS}\n{entry}\n"
    p.write_text(text, encoding="utf-8")


def daily_note(cfg: Config, run_id: str, lines: list[str]) -> Path:
    d = cfg.reports_dir / "daily"
    d.mkdir(parents=True, exist_ok=True)
    p = d / f"{date.today().isoformat()}.md"
    with open(p, "a", encoding="utf-8") as f:
        f.write(f"\n## Run {run_id} ({datetime.now(timezone.utc).strftime('%H:%M UTC')})\n")
        for ln in lines:
            f.write(f"- {ln}\n")
    return p


def system_prefix(cfg: Config, skills: list[str] | None = None) -> str:
    """Stable prefix: SOUL + AGENTS + USER + MEMORY + requested skills. Put variable data AFTER this."""
    core = read_core(cfg)
    parts = [core["SOUL"].strip(), core["AGENTS"].strip(), core["USER"].strip(), core["MEMORY"].strip()]
    for s in skills or []:
        text = read_skill(cfg, s)
        if text:
            parts.append(f"# Skill: {s}\n{text.strip()}")
    return "\n\n".join(p for p in parts if p)
