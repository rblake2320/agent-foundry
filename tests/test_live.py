"""Live tests (real Ollama, real web). Run with AGENTKIT_LIVE=1."""
import json
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "foundry"))

live = pytest.mark.skipif(os.environ.get("AGENTKIT_LIVE") != "1", reason="set AGENTKIT_LIVE=1 for live model/web tests")


@live
def test_web_search_and_fetch_are_real():
    from agentkit.tools import web
    rows = web.search("site:github.com agent2agent protocol agent card", 3)
    assert rows and rows[0]["url"].startswith("http")
    text = web.fetch_text("https://a2a-protocol.org/latest/", 3000)
    assert len(text) > 200


@live
def test_worker_runs_a_real_task_with_tools_and_verifies(cfg):
    """Generic loop on the probe agent: a task that must record a lead via record_put and finish."""
    cfg.model.backend = "ollama"
    cfg.limits.max_model_calls_per_run = 20
    (cfg.root / "tasks" / "probe.md").write_text(
        "---\nname: probe\nschedule: daily\nskills: [probing]\ntools: [record_put, current_time]\ndescription: probe task\n---\n\n"
        "Record one lead in the collection 'leads' with fields company='Example Bakery', status='new', why_fit='hand-written invoices'. "
        "Then finish with a two-line summary naming the company and the current time.\n\n"
        "## Deliverable\n- A lead named Example Bakery exists with status new\n- The summary names the company\n", encoding="utf-8")
    from agentkit.store import Store
    from agentkit.worker import Worker
    res = Worker(cfg).run("probe")
    assert res["status"] == "completed", res
    rec = res["receipt"]
    assert rec["tasks_done"] == 1 and rec["tool_calls"] >= 1
    assert Store(cfg.db).get("leads", "example-bakery")["status"] == "new"


@live
def test_foundry_builds_seller_end_to_end(tmp_path):
    """Full pipeline on a temp copy of the Foundry: DEFINE→…→REGISTER, smoke run included. Slow (minutes)."""
    import shutil
    src = ROOT / "foundry"
    f = tmp_path / "foundry"
    shutil.copytree(src, f, ignore=shutil.ignore_patterns("data", "reports", "__pycache__"))
    (tmp_path / "products").mkdir()
    toml = (f / "agent.toml").read_text(encoding="utf-8").replace('port = 8110', 'port = 8190')
    (f / "agent.toml").write_text(toml, encoding="utf-8")
    from agentkit.cli import load_agent
    cfg, worker_cls, panels = load_agent(f)
    res = worker_cls(cfg).run()
    assert res["status"] == "completed", res
    assert res["receipt"]["built"] == 1
    from agentkit.store import Store
    cat = Store(cfg.db).list("catalog")
    assert cat[0]["slug"] == "agent-seller" and cat[0]["status"] == "verified"
    assert (tmp_path / "products" / "catalog.json").exists()
    assert json.loads((tmp_path / "products" / "catalog.json").read_text(encoding="utf-8"))[0]["pricing"]["model"] == "hybrid"
    assert Path(cat[0]["package"]).exists()
