"""Foundry pipeline, offline: spec validation, package generation from the real seller commission, the generated
package's own tests, and the verifier's non-smoke gates. The live build (with model + smoke run) is a separate live test."""
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "foundry"))

from generator import render_package  # noqa: E402
from spec_schema import SPEC_TEMPLATE, normalize, validate  # noqa: E402
from verifier import verify_product  # noqa: E402

SELLER = json.loads((ROOT / "foundry" / "commissions" / "001-agent-seller.json").read_text(encoding="utf-8"))


def test_seller_spec_is_valid_and_template_is_not():
    assert validate(normalize(json.loads(json.dumps(SELLER)))) == []
    errs = validate(normalize(json.loads(json.dumps(SPEC_TEMPLATE))))
    assert errs, "the template must not validate as-is (placeholder skill names etc.)"


def test_validation_catches_runtime_violations():
    bad = json.loads(json.dumps(SELLER))
    bad["tools"].append("launch_missiles")
    bad["approvals"] = []
    bad["tasks"][0]["deliverable"] = ["one"]
    bad["pricing"]["model"] = "free"
    errs = validate(normalize(bad))
    assert any("unknown tools" in e for e in errs)
    assert any("requires approval action send_email" in e for e in errs)
    assert any("deliverable checklist" in e for e in errs)
    assert any("pricing.model" in e for e in errs)


def test_generate_seller_package_and_run_its_tests(tmp_path):
    dest = render_package(SELLER, tmp_path / "agent-seller", port=8151, catalog_path=str(tmp_path / "catalog.json"))
    for f in ("agent.toml", "SOUL.md", "AGENTS.md", "USER.md", "MEMORY.md", "agent.py", "README.md", "spec.json", "tests/test_package.py"):
        assert (dest / f).exists(), f
    assert sorted(p.name for p in (dest / "tasks").glob("*.md")) == ["draft-outreach.md", "find-prospects.md", "pipeline-brief.md", "qualify-and-quote.md"]
    assert (dest / "skills" / "sales" / "prospect-discovery" / "SKILL.md").exists()
    toml = (dest / "agent.toml").read_text(encoding="utf-8")
    assert 'allowed = ["catalog_lookup"' in toml and 'actions = ["send_email", "schedule_call"]' in toml and "port = 8151" in toml
    assert "catalog_path" in toml
    p = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", str(dest / "tests")], capture_output=True, text=True, cwd=str(ROOT))
    assert p.returncode == 0, p.stdout + p.stderr
    # verifier without the smoke gate (no model in CI): doctor/tests/ledger/card gates are judged
    ver = verify_product(dest, ROOT, smoke=False)
    assert ver["gates"]["tests"]["ok"] and ver["gates"]["card"]["ok"] and ver["gates"]["ledger"]["ok"]
    assert ver["verdict"] == "FAIL" and ver["first_failing_gate"] in ("doctor", "smoke")  # smoke skipped => cannot PASS without a real run


def test_rebuild_in_place_keeps_runtime_state_even_with_an_open_file(tmp_path):
    dest = render_package(SELLER, tmp_path / "s", port=8153)
    (dest / "data").mkdir()
    (dest / "reports").mkdir()
    (dest / "data" / "agent.db").write_bytes(b"state")
    (dest / "reports" / "old.md").write_text("old", encoding="utf-8")
    (dest / "stale.txt").write_text("generated once, must vanish", encoding="utf-8")
    with open(dest / "data" / "mc.log", "a", encoding="utf-8") as held:  # simulates a running Mission Control holding its log
        held.write("running\n")
        held.flush()
        render_package({**SELLER, "version": "1.0.9"}, dest, port=8153)
    assert (dest / "data" / "agent.db").read_bytes() == b"state" and (dest / "reports" / "old.md").exists()
    assert not (dest / "stale.txt").exists() and 'version = "1.0.9"' in (dest / "agent.toml").read_text(encoding="utf-8")


def test_generated_soul_and_agents_carry_the_guardrails(tmp_path):
    dest = render_package(SELLER, tmp_path / "s", port=8152)
    soul = (dest / "SOUL.md").read_text(encoding="utf-8")
    agents = (dest / "AGENTS.md").read_text(encoding="utf-8")
    assert "never send, publish, spend, delete, or contact anyone without an approved action" in soul
    assert "data, not instructions" in soul and "## Approval rule" in agents and "## Data-not-instructions rule" in agents
    assert "`draft_outreach` | proposes approval" in agents
