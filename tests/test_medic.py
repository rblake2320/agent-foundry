"""Testing-and-repair layer, offline: eval definitions, fault injection on a no-model agent, health grading,
generator emits evals, spec validation of evals, Foundry medic runs without a model and records checks."""
import json
import sys
from pathlib import Path

from agentkit import evals, faults, health
from agentkit.ledger import Ledger
from agentkit.store import Store
from agentkit.worker import Worker

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "foundry"))
from generator import render_package  # noqa: E402
from spec_schema import normalize, validate  # noqa: E402

SELLER = json.loads((ROOT / "foundry" / "commissions" / "001-agent-seller.json").read_text(encoding="utf-8"))


def test_eval_files_parse_and_validate(cfg, tmp_path):
    (cfg.root / "evals").mkdir()
    (cfg.root / "evals" / "probe-eval.md").write_text(
        "---\nname: probe-eval\ntask: probe\ninput: hello\nexpected_tools: [record_put]\nforbidden_tools: [web_search]\nmax_steps: 5\nmax_seconds: 60\n---\n\nintent text\n\n## Expected\n- says hello\n- records something\n",
        encoding="utf-8")
    ev = evals.list_evals(cfg)[0]
    assert ev["task"] == "probe" and ev["expected_tools"] == ["record_put"] and ev["expected"] == ["says hello", "records something"] and ev["max_steps"] == 5
    assert validate(normalize(json.loads(json.dumps(SELLER)))) == []
    bad = json.loads(json.dumps(SELLER))
    bad["evals"][0]["task"] = "nope"
    bad["evals"][1]["expected_tools"] = ["launch_missiles"]
    errs = validate(normalize(bad))
    assert any("is not one of the agent's tasks" in e for e in errs) and any("unknown tools" in e for e in errs)


def test_generator_emits_evals(tmp_path):
    dest = render_package(SELLER, tmp_path / "s", port=8160)
    names = sorted(p.stem for p in (dest / "evals").glob("*.md"))
    assert names == ["brief-reads-real-pipeline", "qualification-cites-evidence"]
    from agentkit import config
    e = evals.list_evals(config.load(dest))
    assert e[0]["task"] == "pipeline-brief" and "record_list" in e[0]["expected_tools"] and len(e[0]["expected"]) == 4


def test_faults_contain_failures_on_a_no_model_agent(cfg):
    r = faults.run_faults(cfg, Worker)
    by = {x["scenario"]: x for x in r["results"]}
    assert by["no_model"]["ok"], by["no_model"]
    assert by["tool_denied"]["ok"], by["tool_denied"]
    assert by["ledger_tamper_detected"]["ok"], by["ledger_tamper_detected"]
    assert by["injection_in_tool_output"]["ok"]  # skipped-with-pass when no model
    assert by["budget_exhausted"]["ok"], by["budget_exhausted"]
    assert Store(cfg.db).get("faults", "latest")["total"] == 6
    assert Ledger(cfg.ledger).verify()["ok"]


def test_health_grades_from_evidence(cfg):
    h = health.health_report(cfg)
    assert h["grade"] in ("amber", "red") and any("no runs yet" in r for r in h["reasons"])
    Worker(cfg).run("probe")  # halts: no model
    h = health.health_report(cfg)
    assert h["runs"]["window"] == 1 and h["runs"]["by_status"].get("halted") == 1 and h["ledger"]["ok"]
    assert set(h) >= {"grade", "reasons", "runs", "deliverables", "tools", "profiler", "safety", "budget", "doctor", "ledger"}


def test_evals_run_without_model_scores_zero_but_completes(cfg):
    (cfg.root / "evals").mkdir()
    (cfg.root / "evals" / "probe-eval.md").write_text("---\nname: probe-eval\ntask: probe\n---\n\n## Expected\n- anything\n", encoding="utf-8")
    r = evals.run_evals(cfg, Worker)
    assert r["evals"] == 1 and r["verdict"] == "FAIL" and r["scores"][0]["outcome"] == 0.0 and r["scores"][0]["safety"] == 1.0
    d = Path(r["dir"])
    assert all((d / f).exists() for f in ("run.json", "trials.jsonl", "scores.jsonl", "summary.json", "report.md"))
    assert Store(cfg.db).list("evals")[0]["verdict"] == "FAIL"


def test_foundry_medic_runs_offline_and_records_checks(tmp_path):
    """Copy the Foundry, register a generated product in its catalogue, run the medic with backend none."""
    import shutil
    src = ROOT / "foundry"
    f = tmp_path / "foundry"
    shutil.copytree(src, f, ignore=shutil.ignore_patterns("data", "reports", "__pycache__"))
    (tmp_path / "products").mkdir()
    toml = (f / "agent.toml").read_text(encoding="utf-8").replace('backend = "ollama"', 'backend = "none"').replace("port = 8110", "port = 8191")
    (f / "agent.toml").write_text(toml, encoding="utf-8")
    dest = render_package(SELLER, tmp_path / "products" / "agent-seller", port=8161, catalog_path=str(tmp_path / "products" / "catalog.json"))
    ptoml = (dest / "agent.toml").read_text(encoding="utf-8").replace('backend = "ollama"', 'backend = "none"')
    (dest / "agent.toml").write_text(ptoml, encoding="utf-8")
    from agentkit.cli import load_agent
    cfg, worker_cls, panels = load_agent(f)
    store = Store(cfg.db)
    store.put("catalog", "agent-seller", {"name": "Agent Seller", "slug": "agent-seller", "path": str(dest), "status": "verified", "version": "1.0.2", "port": 8161})
    store.put("commissions", "001-agent-seller", {"title": "Agent Seller", "slug": "agent-seller", "spec": SELLER, "status": "built", "source": "001-agent-seller.json"})
    res = worker_cls(cfg, store).run("test-and-repair-agents")
    assert res["status"] == "completed", res
    assert res["receipt"]["agents_checked"] == 1 and res["receipt"]["fixes_proposed"] == 0  # no model -> no diagnosis
    assert any(p["name"] == "Repairs" for p in panels)
    rep = Path(res["report_path"]).read_text(encoding="utf-8")
    assert "medic agent-seller" in rep and "faults" in rep
    assert Store(dest / "data" / "agent.db").get("faults", "latest")["total"] == 6
