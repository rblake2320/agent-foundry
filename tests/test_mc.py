"""Generic Mission Control against a real app + temp agent (FastAPI TestClient)."""
from fastapi.testclient import TestClient

from agentkit.ledger import Ledger
from agentkit.mc import create_app
from agentkit.store import Store
from agentkit.worker import Worker

PANELS = [{"name": "Leads", "collection": "leads", "columns": ["company", "status"], "actions": [{"id": "bump", "label": "Bump"}],
           "handler": lambda cfg, store, ledger, doc_id, action_id: {"bumped": doc_id, "action": action_id}}]


def test_all_endpoints(cfg):
    store, ledger = Store(cfg.db), Ledger(cfg.ledger)
    store.put("leads", "acme", {"company": "Acme", "status": "new"})
    store.create_approval("seed", "send_email", "draft-x", "why")
    c = TestClient(create_app(cfg, Worker, PANELS))
    assert b"Mission Control" in c.get("/").content
    s = c.get("/api/status").json()
    assert s["agent"]["slug"] == "probe-agent" and s["running"] is False and s["pending_approvals"] == 1
    assert s["panels"][0]["name"] == "Leads" and s["collections"] == {"leads": 1} and s["ledger"]["ok"] is True
    card = c.get("/.well-known/agent-card.json").json()
    assert card["name"] == "Probe Agent" and card["skills"][0]["id"] == "probe" and card["supportedInterfaces"][0]["protocolBinding"] == "HTTP+JSON"
    assert c.get("/.well-known/agent.json").json()["version"] == "0.1.0"
    assert c.get("/api/tasks").json()[0]["name"] == "probe"
    tools = c.get("/api/tools").json()
    assert any(t["name"] == "draft_outreach" and t["approval_action"] == "send_email" for t in tools)
    assert c.get("/api/docs/leads").json()[0]["company"] == "Acme"
    assert c.put("/api/docs/leads/acme", json={"fields": {"status": "qualified"}}).json()["status"] == "qualified"
    assert c.post("/api/panel/leads/acme/bump").json() == {"bumped": "acme", "action": "bump"}
    a = c.get("/api/approvals").json()
    assert a[0]["status"] == "pending" and "description" in a[0]
    assert c.post(f"/api/approvals/{a[0]['id']}/deny").json()["status"] == "denied"
    assert c.post(f"/api/approvals/{a[0]['id']}/approve").status_code == 400
    b = c.get("/api/brain").json()
    assert set(b["files"]) == {"SOUL", "AGENTS", "USER", "MEMORY"} and b["skills"][0]["name"] == "probing"
    assert c.put("/api/brain/USER", json={"content": "# USER\n- edited\n"}).status_code == 200
    assert "edited" in c.get("/api/brain/USER").json()["content"]
    assert c.get("/api/skills/probing").status_code == 200
    assert c.get("/api/report/latest").json()["content"] is None
    d = c.get("/api/doctor").json()
    assert any(ch["name"] == "core files SOUL/AGENTS/USER/MEMORY present" and ch["ok"] for ch in d["checks"])
    # run via API with backend none -> halts cleanly, report exists, no 5xx anywhere
    assert c.post("/api/run", json={"task": "probe"}).json()["started"] is True
    import time
    for _ in range(100):
        time.sleep(0.1)
        if not c.get("/api/run/progress").json()["running"]:
            break
    runs = c.get("/api/runs").json()
    assert runs[0]["status"] == "halted" and "no model configured" in runs[0]["halt_reason"]
    assert "run report" in c.get("/api/report/latest").json()["content"]
    assert c.get("/api/activity/verify").json()["ok"] is True
    assert c.delete("/api/docs/leads/acme").json()["ok"] is True and c.get("/api/docs/leads/acme").status_code == 404


def test_token_gate(cfg, monkeypatch):
    monkeypatch.setenv("AGENTKIT_MC_TOKEN", "t0k")
    c = TestClient(create_app(cfg, Worker, []))
    assert c.get("/api/status").json()["auth_required"] is True
    assert c.put("/api/brain/USER", json={"content": "x"}).status_code == 401
    assert c.post("/api/run", json={}).status_code == 401
    assert c.put("/api/brain/USER", json={"content": "x"}, headers={"X-Agent-Token": "t0k"}).status_code == 200
