import shutil
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "foundry"))

from agentkit import config  # noqa: E402

MINIMAL_TOML = """
[agent]
name = "Probe Agent"
slug = "probe-agent"
version = "0.1.0"
description = "test agent"
responsibility = "prove the harness"
audience = "tests"

[model]
backend = "none"

[limits]
max_model_calls_per_run = 5
max_run_minutes = 5

[tools]
allowed = ["record_put", "record_get", "record_list", "note_write", "read_file", "current_time", "catalog_lookup", "quote_price", "draft_outreach", "propose_action"]

[approvals]
actions = ["send_email", "publish_agent"]

[schedule]
time = "07:15"

[paths]
inbox = "reports"
db = "data/agent.db"
ledger = "data/ledger.jsonl"

[mission_control]
host = "127.0.0.1"
port = 8199
"""


@pytest.fixture
def agent_dir(tmp_path: Path) -> Path:
    d = tmp_path / "probe"
    (d / "skills" / "general" / "probing").mkdir(parents=True)
    (d / "tasks").mkdir()
    (d / "agent.toml").write_text(MINIMAL_TOML, encoding="utf-8")
    for n in ("SOUL", "AGENTS", "USER"):
        (d / f"{n}.md").write_text(f"# {n}.md\n\n{n} content\n", encoding="utf-8")
    (d / "MEMORY.md").write_text("# MEMORY.md\n\n## Lessons\n- 2026-01-01: created\n\n## Owner decisions\n", encoding="utf-8")
    (d / "skills" / "general" / "probing" / "SKILL.md").write_text(
        "---\nname: probing\ndescription: probe things\n---\n\n# Probing\n\n## When to Use\n- always\n\n## Quick Reference\n- x\n\n## Procedure\n1. a\n2. b\n\n## Pitfalls\n- none\n\n## Verification\n- [ ] done\n",
        encoding="utf-8")
    (d / "tasks" / "probe.md").write_text(
        "---\nname: probe\nschedule: daily\nskills: [probing]\ntools: [record_put, current_time]\ndescription: probe task\n---\n\nDo the probe.\n\n## Deliverable\n- item one\n- item two\n",
        encoding="utf-8")
    return d


@pytest.fixture
def cfg(agent_dir: Path):
    return config.load(agent_dir)
