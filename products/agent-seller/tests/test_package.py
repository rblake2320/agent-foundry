"""Hermetic checks generated with the package: it loads, its brain is complete, its tools are allowlisted, its card is valid."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
KIT = (ROOT / "../..").resolve()   # the agentkit repo root, relative to this package
sys.path.insert(0, str(KIT))

from agentkit import brain, config  # noqa: E402
from agentkit.mc import agent_card  # noqa: E402
from agentkit.tools import REGISTRY  # noqa: E402


def test_config_and_core_files():
    cfg = config.load(ROOT)
    assert cfg.agent.slug == "agent-seller"
    assert all(p.exists() for p in cfg.core_files.values())


def test_tasks_skills_tools():
    cfg = config.load(ROOT)
    tasks = brain.list_tasks(cfg)
    assert sorted(t["name"] for t in tasks) == ["draft-outreach", "find-prospects", "pipeline-brief", "qualify-and-quote"]
    for t in tasks:
        assert len(t["deliverable"]) >= 2
        assert all(x in cfg.tools_allowed for x in t["tools"])
    assert all(t in REGISTRY for t in cfg.tools_allowed)
    names = {s["name"] for s in brain.list_skills(cfg)}
    for t in tasks:
        assert set(t["skills"]) <= names


def test_agent_card():
    cfg = config.load(ROOT)
    card = agent_card(cfg)
    for k in ("name", "description", "supportedInterfaces", "version", "capabilities", "defaultInputModes", "defaultOutputModes", "skills"):
        assert k in card
    assert card["supportedInterfaces"][0]["protocolBinding"] == "HTTP+JSON"
    assert card["skills"] and all(s["tags"] is not None for s in card["skills"])
