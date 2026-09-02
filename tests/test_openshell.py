"""OpenShell export: policy derived from the allowlist, deny-by-default, approval actions never pre-authorized,
child (product) policy is a subset of the builder's, generated packages carry openshell/policy.yaml."""
import json
import sys
from pathlib import Path

from agentkit import config, openshell

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "foundry"))
from generator import render_package  # noqa: E402

SELLER = json.loads((ROOT / "foundry" / "commissions" / "001-agent-seller.json").read_text(encoding="utf-8"))


def test_policy_from_allowlist_and_yaml_shape(cfg):
    pol = openshell.policy_for(cfg)  # probe agent: no web tools
    assert pol["version"] == 1 and pol["network_policies"] == {} and "/sandbox/data" in pol["filesystem_policy"]["read_write"]
    assert pol["_derived_from"]["not_preauthorized"] == {"send_email": openshell.APPROVAL_HOSTS["send_email"]}
    y = openshell.to_yaml(pol)
    assert "version: 1" in y and "filesystem_policy:" in y and "landlock:" in y and "network_policies: {}" in y
    cfg.tools_allowed = list(cfg.tools_allowed) + ["web_search", "web_fetch"]
    pol2 = openshell.policy_for(cfg)
    hosts = {e["host"] for e in pol2["network_policies"]["tool_egress"]["endpoints"]}
    assert "html.duckduckgo.com" in hosts and all(e["access"] == "read-only" and e["enforcement"] == "enforce" for e in pol2["network_policies"]["tool_egress"]["endpoints"])
    assert pol2["network_policies"]["tool_egress"]["binaries"] == openshell.PY_BINARIES
    y2 = openshell.to_yaml(pol2)
    assert "- host: html.duckduckgo.com" in y2 and "- { path: /usr/bin/python3 }" in y2
    try:
        import yaml  # optional: if PyYAML is present, the emitted document must parse to the same shape
        doc = yaml.safe_load(y2)
        assert doc["version"] == 1 and doc["network_policies"]["tool_egress"]["endpoints"][0]["port"] == 443
    except ImportError:
        pass


def test_generated_product_carries_policy_and_is_subset_of_foundry(tmp_path):
    dest = render_package(SELLER, tmp_path / "s", port=8170)
    pol_path = dest / "openshell" / "policy.yaml"
    assert pol_path.exists() and (dest / "openshell" / "RUN_UNDER_OPENSHELL.md").exists()
    seller = openshell.policy_for(config.load(dest))
    foundry = openshell.policy_for(config.load(ROOT / "foundry"))
    # the builder itself needs no tool egress; the product's filesystem grants are the same fixed set (no widening)
    assert foundry["network_policies"] == {}
    assert seller["filesystem_policy"] == foundry["filesystem_policy"]
    assert "send_email" in seller["_derived_from"]["not_preauthorized"]
    assert "duckduckgo" in pol_path.read_text(encoding="utf-8")
