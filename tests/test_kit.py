"""Harness core, offline: config, store, ledger, brain, tools, approvals, schedule helpers, budget gate."""
import json
import os
from pathlib import Path

import pytest

from agentkit import approvals, brain, schedule
from agentkit.ledger import Ledger
from agentkit.model import BudgetExceeded, ModelClient, ModelError
from agentkit.store import Store
from agentkit.tools import REGISTRY, ToolContext, allowed_tools, run_tool
from agentkit.worker import Worker


def test_config_and_brain(cfg):
    assert cfg.agent.slug == "probe-agent" and cfg.mc_port == 8199 and cfg.schedule_time == "07:15"
    core = brain.read_core(cfg)
    assert all(core.values())
    skills = brain.list_skills(cfg)
    assert skills[0]["name"] == "probing" and skills[0]["category"] == "general"
    tasks = brain.list_tasks(cfg)
    assert tasks[0]["name"] == "probe" and tasks[0]["deliverable"] == ["item one", "item two"] and tasks[0]["tools"] == ["record_put", "current_time"]
    assert brain.read_task(cfg, "probe")["schedule"] == "daily"
    prefix = brain.system_prefix(cfg, ["probing"])
    assert "SOUL content" in prefix and "# Skill: probing" in prefix and prefix.index("SOUL content") < prefix.index("# Skill")


def test_memory_lessons_and_decisions(cfg):
    for i in range(45):
        brain.remember_lesson(cfg, f"lesson {i}")
    brain.remember_decision(cfg, "ship it")
    text = cfg.core_files["MEMORY"].read_text(encoding="utf-8")
    lessons = [ln for ln in text.split("## Owner decisions")[0].splitlines() if ln.startswith("- ")]
    assert len(lessons) == 40 and lessons[-1].endswith("lesson 44")
    assert "ship it" in text.split("## Owner decisions")[1]


def test_store_docs_runs_approvals_budget(cfg):
    s = Store(cfg.db)
    s.put("leads", "acme", {"company": "Acme", "status": "new"})
    s.put("leads", "acme", {"company": "Acme", "status": "qualified"})
    assert s.get("leads", "acme")["status"] == "qualified" and s.list("leads", status="qualified")[0]["id"] == "acme"
    assert s.collections() == ["leads"]
    s.create_run("r1", "test")
    s.finish_run("r1", "completed", "done", {"x": 1}, "sum", None)
    assert s.get_run("r1")["receipt"] == {"x": 1} and s.running_run() is None
    a = s.create_approval("r1", "send_email", "draft-1", "why", {"to": "x@example.com"})
    assert s.create_approval("r1", "send_email", "draft-1", "dup") == a
    assert s.get_approval(a)["payload"]["to"] == "x@example.com"
    s.add_budget(2, 100, 50)
    assert s.month_budget()["model_calls"] == 2


def test_ledger_chain_and_tamper(tmp_path):
    lg = Ledger(tmp_path / "l.jsonl")
    for i in range(3):
        lg.append("e", "r", i=i)
    assert lg.verify() == {"ok": True, "count": 3, "first_bad_line": None}
    lines = (tmp_path / "l.jsonl").read_text(encoding="utf-8").splitlines()
    row = json.loads(lines[1])
    row["detail"]["i"] = 99
    lines[1] = json.dumps(row)
    (tmp_path / "l.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")
    assert lg.verify()["ok"] is False and lg.verify()["first_bad_line"] == 2


def test_tools_allowlist_records_and_proposals(cfg):
    store, ledger = Store(cfg.db), Ledger(cfg.ledger)
    ctx = ToolContext(cfg, store, ledger, "r1", "probe")
    tools = allowed_tools(cfg)
    assert "web_search" not in tools and "record_put" in tools
    assert run_tool(ctx, "web_search", {"query": "x"}, tools, 500).startswith("ERROR: tool 'web_search' is not in this agent's allowlist")
    out = run_tool(ctx, "record_put", {"collection": "leads", "fields": {"company": "Acme Co", "status": "new"}}, tools, 500)
    assert "saved leads/acme-co" in out and store.get("leads", "acme-co")["status"] == "new"
    assert "Acme Co" in run_tool(ctx, "record_list", {"collection": "leads", "field": "status", "value": "new"}, tools, 500)
    assert run_tool(ctx, "read_file", {"path": "../../etc/passwd"}, tools, 500).startswith("ERROR: path outside")
    assert "SOUL content" in run_tool(ctx, "read_file", {"path": "SOUL.md"}, tools, 500)
    # catalog + quote + outreach (proposes, never sends)
    (cfg.root / "catalog.json").write_text(json.dumps([{"name": "Agent Seller", "slug": "agent-seller", "status": "verified", "version": "1.0.0",
                                                         "description": "sells", "pricing": {"model": "hybrid", "price": 249, "unit": "month"}}]), encoding="utf-8")
    cfg.extra["catalog_path"] = "catalog.json"
    assert "agent-seller" in run_tool(ctx, "catalog_lookup", {"keyword": "seller"}, tools, 800)
    q = run_tool(ctx, "quote_price", {"agent_slug": "agent-seller", "prospect": "Acme Co", "pricing_model": "hybrid", "volume": 200, "tier": "mid"}, tools, 2000)
    assert q.startswith("quote q-") and store.list("quotes")[0]["monthly_estimate"] == pytest.approx(49 * 5 + 0.20 * 200)
    assert run_tool(ctx, "quote_price", {"agent_slug": "nope", "prospect": "x"}, tools, 500).startswith("ERROR: unknown agent")
    d = run_tool(ctx, "draft_outreach", {"to": "unknown", "prospect": "Acme Co", "subject": "Your weekly X", "body": "hello"}, tools, 500)
    assert "approval #" in d and "PENDING" in d
    pend = store.list_approvals("pending")
    assert pend[0]["action"] == "send_email" and store.list("outbox")[0]["status"] == "awaiting_approval"
    assert (cfg.data_dir / "outbox").exists()
    assert run_tool(ctx, "propose_action", {"action": "delete_everything", "target": "x", "rationale": "y"}, tools, 500).startswith("ERROR")
    assert "PENDING" in run_tool(ctx, "propose_action", {"action": "publish_agent", "target": "agent-seller", "rationale": "y"}, tools, 500)
    out = run_tool(ctx, "record_put", {"collection": "x", "fields": "not json"}, tools, 500)
    assert out.startswith("ERROR")


def test_approvals_gate_and_send_email_without_smtp(cfg, monkeypatch):
    store, ledger = Store(cfg.db), Ledger(cfg.ledger)
    store.put("outbox", "draft-1", {"to": "x@example.com", "subject": "s", "body": "b", "status": "awaiting_approval"})
    aid = store.create_approval("r1", "send_email", "draft-1", "why")
    with pytest.raises(ValueError):
        approvals.execute(cfg, store, ledger, aid)
    for k in ("SMTP_HOST", "SMTP_USER", "SMTP_PASSWORD", "SMTP_FROM"):
        monkeypatch.delenv(k, raising=False)
    approvals.decide(store, ledger, aid, True)
    res = approvals.execute(cfg, store, ledger, aid)
    assert res["ok"] is False and "SMTP not configured" in res["result"]
    assert store.get("outbox", "draft-1")["status"] == "approved_not_sent" and store.get_approval(aid)["status"] == "failed"
    with pytest.raises(ValueError):
        approvals.decide(store, ledger, aid, False)  # decisions are final
    b = store.create_approval("r1", "mystery_action", "t", "no executor")
    approvals.decide(store, ledger, b, True)
    assert approvals.execute(cfg, store, ledger, b)["result"] == "recorded only"


def test_budget_gate_and_none_backend(cfg):
    store = Store(cfg.db)
    m = ModelClient(cfg, store)
    assert m.available is False
    with pytest.raises(ModelError):
        m.complete("s", "u")
    cfg.model.backend = "ollama"
    cfg.limits.max_model_calls_per_run = 0
    with pytest.raises(BudgetExceeded):
        ModelClient(cfg, store).complete("s", "u")
    w = Worker(cfg)
    cfg.model.backend = "none"
    res = w.run("probe")
    assert res["status"] == "halted" and "no model configured" in res["halt_reason"]
    assert Path(res["report_path"]).exists() and Ledger(cfg.ledger).verify()["ok"]


def test_schedule_helpers_and_wrapper(cfg, tmp_path):
    w = tmp_path / "run.sh"
    tag = schedule.cron_tag(cfg)
    line = schedule.cron_line(w, "07:15", tag)
    ct = schedule.cron_add("0 1 * * * /bin/backup\n", line, tag)
    assert ct.count(tag) == 1 and schedule.cron_parse(ct, tag)["hour"] == "7"
    assert tag not in schedule.cron_remove(ct, tag)
    assert schedule.install(cfg, "7pm")["ok"] is False
    wp = schedule.write_wrapper(cfg)
    assert wp.exists() and "-m agentkit --root" in wp.read_text(encoding="utf-8")
    assert schedule.task_name(cfg) == "AgentFoundry-probe-agent"
