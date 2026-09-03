"""Trust layer, offline and real (no mocks): keys, signed evidence bundles, approvals→SD-JWT mandates, agent recall, model failover."""
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from agentkit import approvals, config, evidence, mandates, recall
from agentkit.keys import KeyStore
from agentkit.ledger import Ledger
from agentkit.model import ModelClient, ModelError
from agentkit.store import Store


def test_keys_identity_and_signatures(cfg):
    k = KeyStore(cfg)
    assert k.did.startswith("did:agentkit:probe-agent:") and KeyStore(cfg).did == k.did  # stable across reloads
    sig = k.sign(b"hello")
    assert KeyStore.verify(k.identity_public_b64, b"hello", sig) and not KeyStore.verify(k.identity_public_b64, b"hellp", sig)
    es = k.sign_es256(b"payload")
    assert KeyStore.verify_es256(k.p256_jwk, b"payload", es) and not KeyStore.verify_es256(k.p256_jwk, b"payloaD", es)
    assert (cfg.data_dir / "keys" / "identity_ed25519.pem").exists() and (cfg.data_dir / "keys" / "mandate_p256.pem").exists()
    assert k.p256_jwk["crv"] == "P-256" and k.p256_jwk["kid"] == "probe-agent-mandate-key-1"


def test_evidence_bundle_signs_and_verifies_offline(cfg):
    store, ledger = Store(cfg.db), Ledger(cfg.ledger)
    ledger.append("probe", None)
    out = evidence.build_bundle(cfg, store, ledger)
    assert all((out / f).exists() for f in ("manifest.json", "agent-manifest.yaml", "signature.json"))
    v = evidence.verify_bundle(out)
    assert v["ok"] and v["signer"] == KeyStore(cfg).did and v["files"] >= 2
    m = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
    assert m["format"] == evidence.FORMAT and m["agent"]["did"] == v["signer"]
    assert m["build"]["skills"][0]["name"] == "probing" and m["build"]["tasks"][0]["deliverable_items"] == 2
    assert m["authority"]["approval_actions"] == ["send_email", "publish_agent"] and m["ledger"]["ok"] and m["ledger"]["events"] == 1
    assert evidence.latest_bundle(cfg, store)["id"] == out.name and ledger.read(limit=1)[0]["event"] == "evidence_bundle"
    yaml = (out / "agent-manifest.yaml").read_text(encoding="utf-8")
    assert "did: did:agentkit:probe-agent:" in yaml and "tools_allowed: [record_put" in yaml
    # tamper with the manifest → offline verification fails, signature file untouched
    p = out / "manifest.json"
    p.write_text(p.read_text(encoding="utf-8").replace('"probe-agent"', '"probe-agent-x"'), encoding="utf-8")
    v2 = evidence.verify_bundle(out)
    assert not v2["ok"] and any("manifest" in r for r in v2["reasons"])
    # tamper with the signature → fails
    sig = json.loads((out / "signature.json").read_text(encoding="utf-8"))
    sig["payload"]["signer"] = "did:agentkit:someone-else"
    (out / "signature.json").write_text(json.dumps(sig), encoding="utf-8")
    assert "signature does not verify" in evidence.verify_bundle(out)["reasons"]


def test_approval_becomes_signed_mandate(cfg):
    store, ledger = Store(cfg.db), Ledger(cfg.ledger)
    aid = store.create_approval("run-1", "send_email", "draft-1", "send it", {"to": "x@example.com"})
    a = approvals.decide(store, ledger, aid, True, who="owner", cfg=cfg)
    assert a["status"] == "approved" and a["mandate"] and a["mandate"].endswith("~")
    v = mandates.verify_mandate(a["mandate"])
    assert v["ok"] and v["vct"] == mandates.APPROVAL_VCT and v["issuer"] == KeyStore(cfg).did and v["kid"] == "probe-agent-mandate-key-1"
    assert v["claims"]["action"] == "send_email" and v["claims"]["target"] == "draft-1" and v["claims"]["approver"] == "owner"
    assert v["claims"]["approval_id"] == aid and len(v["claims"]["payload_sha256"]) == 64
    assert ledger.read(limit=1)[0]["detail"]["mandate_kid"] == "probe-agent-mandate-key-1"
    # a swapped disclosure is rejected (digest not in _sd)
    jwt, *disc = a["mandate"].split("~")
    forged = jwt + "~" + "~".join([mandates._disclosure("action", "publish_agent")] + [d for d in disc[1:] if d]) + "~"
    assert mandates.verify_mandate(forged)["reason"] == "disclosure digest not in _sd"
    # a different key does not verify it
    jwk = dict(KeyStore(cfg).p256_jwk)
    assert mandates.verify_mandate(a["mandate"], jwk={**jwk, "x": jwk["y"]})["reason"] == "signature invalid"
    # expiry is enforced
    assert mandates.verify_mandate(mandates.issue_approval_mandate(cfg, a, ttl_s=-5))["reason"] == "expired"
    # garbage is reported, not crashed on
    assert mandates.verify_mandate("not.a.jwt~")["ok"] is False
    # AP2-shaped open payment mandate carries constraints
    pm = mandates.verify_mandate(mandates.issue_payment_open_mandate(cfg, {"name": "Acme", "id": "acme"}, 24900))
    assert pm["ok"] and pm["vct"] == mandates.PAYMENT_OPEN_VCT and pm["claims"]["constraints"][0]["max"] == 24900
    assert pm["claims"]["constraints"][1]["allowed"][0]["id"] == "acme"
    # denied approvals get no mandate
    aid2 = store.create_approval("run-1", "publish_agent", "thing", "x")
    assert approvals.decide(store, ledger, aid2, False, cfg=cfg)["mandate"] is None


def test_agent_recall_quarantines_derived_work(cfg):
    store, ledger = Store(cfg.db), Ledger(cfg.ledger)
    store.create_run("run-bad", "probe")
    store.finish_run("run-bad", "completed", "done", {"model": "qwen-bad", "tasks": ["probe"]}, None, None)
    store.create_run("run-ok", "probe")
    store.finish_run("run-ok", "completed", "done", {"model": "qwen-good", "tasks": ["probe"]}, None, None)
    store.put("prospects", "p1", {"name": "Acme", "status": "open", "created_by_run": "run-bad"})
    store.put("prospects", "p2", {"name": "Beta", "status": "open", "created_by_run": "run-ok"})
    store.put("quotes", "q1", {"prospect_id": "p1", "status": "drafted", "created_by_run": "run-ok"})   # derived from p1 in a later, good run
    store.put("outbox", "d1", {"quote_id": "q1", "to": "a@acme.test", "status": "draft", "created_by_run": "run-ok"})
    aid = store.create_approval("run-ok", "send_email", "d1", "send")
    aid_ok = store.create_approval("run-ok", "publish_agent", "p2", "unrelated")
    imp = recall.impact(cfg, store, ledger, "run", "run-bad")
    assert {a["node"] for a in imp["affected"]} == {"run:run-bad", "record:prospects/p1", "record:quotes/q1", "record:outbox/d1", f"approval:{aid}"}
    assert max(a["hops"] for a in imp["affected"]) == 4 and imp["counts"] == {"runs": 1, "records": 3, "approvals": 1}
    assert recall.impact(cfg, store, ledger, "model", "qwen-bad")["seeds_found"] == ["run:run-bad"]
    assert recall.impact(cfg, store, ledger, "task", "probe")["counts"]["runs"] == 2
    with pytest.raises(ValueError):
        recall.impact(cfg, store, ledger, "planet", "x")
    r = recall.recall(cfg, store, ledger, "model", "qwen-bad", "model produced fabricated company data")
    adv = r["advisory"]
    assert set(adv["quarantined_records"]) == {"record:prospects/p1", "record:quotes/q1", "record:outbox/d1"} and adv["denied_approvals"] == [aid]
    assert store.get("prospects", "p1")["status"] == "recalled" and store.get("prospects", "p1")["recall_prior_status"] == "open"
    assert store.get("prospects", "p2")["status"] == "open"
    assert store.get_approval(aid)["status"] == "denied" and store.get_approval(aid_ok)["status"] == "pending"
    path = cfg.data_dir / "advisories" / f"{adv['id']}.json"
    assert recall.verify_advisory(path)["ok"] and adv["issuer"] == KeyStore(cfg).did
    assert ledger.verify()["ok"] and ledger.read(limit=1)[0]["event"] == "recall_issued"
    assert store.get("advisories", adv["id"])["affected_count"] == 5
    lifted = recall.lift(cfg, store, ledger, adv["id"], "model replaced; records re-checked by hand")
    assert len(lifted["restored"]) == 3 and store.get("quotes", "q1")["status"] == "drafted" and "recalled_by" not in store.get("quotes", "q1")
    assert store.get_approval(aid)["status"] == "denied"          # denied stays denied
    assert store.get("advisories", adv["id"])["status"] == "lifted" and ledger.read(limit=1)[0]["event"] == "recall_lifted"
    with pytest.raises(ValueError):
        recall.lift(cfg, store, ledger, adv["id"], "again")


class _OllamaLike(BaseHTTPRequestHandler):
    """A real HTTP server speaking Ollama's /api/chat shape (the standby box in the failover test)."""

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))))
        content = json.dumps({"echo": body["messages"][-1]["content"], "model": body["model"]})
        out = json.dumps({"message": {"role": "assistant", "content": content}, "prompt_eval_count": 7, "eval_count": 3}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(out)))
        self.end_headers()
        self.wfile.write(out)

    def log_message(self, *a):
        pass


def test_model_failover_to_standby_backend(cfg):
    srv = ThreadingHTTPServer(("127.0.0.1", 0), _OllamaLike)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        cfg.model.backend, cfg.model.ollama_url, cfg.model.ollama_model = "ollama", "http://127.0.0.1:9", "primary-model"   # nothing listens on :9
        cfg.model.fallback = config.ModelCfg(backend="ollama", ollama_url=f"http://127.0.0.1:{srv.server_port}", ollama_model="standby-model")
        store = Store(cfg.db)
        m = ModelClient(cfg, store)
        assert m.available and m.name == "primary-model"
        obj = m.complete_json("sys", "ping")
        assert obj == {"echo": "ping", "model": "standby-model"}
        u = m.usage()
        assert u["failovers"] == 1 and u["model"] == "standby-model" and u["primary"] == "primary-model" and "unreachable" in u["failover_reason"]
        assert store.month_budget()["model_calls"] == 1 and store.month_budget()["tokens_in"] == 7
        m.complete("sys", "again")
        assert m.usage()["failovers"] == 1 and m.calls == 2          # stays on the standby, no second failover
        cfg.model.fallback = None
        with pytest.raises(ModelError):                              # no standby → the failure surfaces honestly
            ModelClient(cfg, store).complete("sys", "x")
    finally:
        srv.shutdown()


def test_config_fallback_table(agent_dir):
    toml = (agent_dir / "agent.toml").read_text(encoding="utf-8").replace(
        '[model]\nbackend = "none"',
        '[model]\nbackend = "ollama"\nollama_url = "http://primary-box:11434"\n\n[model.fallback]\nbackend = "openai_compat"\n'
        'openai_base_url = "https://integrate.api.nvidia.com/v1"\nopenai_model = "nvidia/nemotron-3-super-120b-a12b"')
    (agent_dir / "agent.toml").write_text(toml, encoding="utf-8")
    c = config.load(agent_dir)
    assert c.model.backend == "ollama" and c.model.ollama_url == "http://primary-box:11434"
    assert c.model.fallback.backend == "openai_compat" and c.model.fallback.openai_model.startswith("nvidia/")
    assert config.load(agent_dir.parent / "probe").model.fallback is not None
