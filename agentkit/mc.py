"""Mission Control for any agentkit agent: FastAPI app factory + one-file dashboard.
Generic tabs (Overview, Tasks, Runs, Approvals, Activity, Brain, Schedule, Doctor, Report, Chat) plus agent-specific
panels (collections rendered as tables with optional actions) and an A2A agent card at /.well-known/agent-card.json."""
from __future__ import annotations

import json
import os
import threading
from pathlib import Path

from fastapi import Depends, FastAPI, Header, HTTPException, Query
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel

from . import approvals, brain, doctor, schedule
from .config import Config
from .ledger import Ledger
from .model import BudgetExceeded, ModelClient, ModelError
from .store import Store
from .tools import REGISTRY, allowed_tools

STATIC = Path(__file__).parent / "static"


class RunRequest(BaseModel):
    task: str | None = None
    input: str = ""


class BrainFile(BaseModel):
    content: str


class ChatRequest(BaseModel):
    question: str


class ScheduleRequest(BaseModel):
    time: str | None = None


class DocRequest(BaseModel):
    fields: dict


class A2AMessage(BaseModel):
    skill: str            # task name
    input: str = ""


def agent_card(cfg: Config) -> dict:
    """A2A v1.0 Agent Card: one skill per task, HTTP+JSON interface at /a2a."""
    tasks = brain.list_tasks(cfg)
    return {
        "name": cfg.agent.name,
        "description": cfg.agent.description or cfg.agent.responsibility,
        "supportedInterfaces": [{"url": f"http://{cfg.mc_host}:{cfg.mc_port}/a2a", "protocolBinding": "HTTP+JSON", "protocolVersion": "1.0"}],
        "provider": {"organization": cfg.agent.organization, "url": f"http://{cfg.mc_host}:{cfg.mc_port}/"},
        "version": cfg.agent.version,
        "documentationUrl": f"http://{cfg.mc_host}:{cfg.mc_port}/",
        "capabilities": {"streaming": False, "pushNotifications": False, "extendedAgentCard": False},
        "defaultInputModes": ["text/plain"],
        "defaultOutputModes": ["text/markdown", "application/json"],
        "skills": [{"id": t["name"], "name": t["name"].replace("-", " ").title(), "description": t.get("description") or t["body"][:200].strip(),
                    "tags": list(t.get("skills", [])) + list(t.get("tools", [])), "examples": [f"Run task {t['name']}"],
                    "inputModes": ["text/plain"], "outputModes": ["text/markdown"]} for t in tasks],
    }


def chat_answer(cfg: Config, store: Store, model: ModelClient, question: str) -> str:
    last = store.list_runs(limit=1)
    state = {"agent": cfg.agent.name, "last_run": last[0] if last else None, "pending_approvals": store.list_approvals("pending")[:20],
             "tasks": [t["name"] for t in brain.list_tasks(cfg)], "collections": {c: len(store.list(c)) for c in store.collections()},
             "budget": store.month_budget()}
    system = brain.system_prefix(cfg) + ("\n\n# Task\nAnswer the owner's question about your current state using ONLY the state given. "
                                         "Be terse. You cannot take actions; point to the approvals queue or a task instead.")
    return model.complete(system, f"STATE (data):\n{json.dumps(state, default=str)[:12000]}\n\nOWNER QUESTION: {question[:1000]}").strip()


def create_app(cfg: Config, worker_cls, panels: list[dict] | None = None) -> FastAPI:
    store, ledger = Store(cfg.db), Ledger(cfg.ledger)
    state: dict = {"worker": None, "thread": None}
    token = os.environ.get("AGENTKIT_MC_TOKEN", "")
    panels = panels or []

    def require_token(x_agent_token: str | None = Header(default=None)) -> None:
        if token and x_agent_token != token:
            raise HTTPException(401, "X-Agent-Token required")

    app = FastAPI(title=f"{cfg.agent.name} — Mission Control", version=cfg.agent.version)

    @app.get("/")
    def index():
        return FileResponse(STATIC / "index.html")

    @app.get("/.well-known/agent-card.json")
    def card():
        return JSONResponse(agent_card(cfg), media_type="application/json")

    @app.get("/.well-known/agent.json")
    def card_legacy():
        return JSONResponse(agent_card(cfg), media_type="application/json")

    @app.post("/a2a", dependencies=[Depends(require_token)])
    def a2a(msg: A2AMessage):
        if state["thread"] and state["thread"].is_alive():
            raise HTTPException(409, "a run is in progress")
        w = worker_cls(cfg, store, ledger)
        state["worker"] = w
        res = w.run(msg.skill, msg.input)
        return {"taskId": res.get("id"), "status": {"state": "TASK_STATE_COMPLETED" if res.get("status") == "completed" else "TASK_STATE_FAILED"},
                "artifacts": [{"name": "report", "parts": [{"kind": "text", "text": Path(res["report_path"]).read_text(encoding="utf-8")
                                                               if res.get("report_path") and Path(res["report_path"]).exists() else ""}]}],
                "receipt": res.get("receipt")}

    @app.get("/api/status")
    def status():
        w = state["worker"]
        running = bool(state["thread"] and state["thread"].is_alive())
        progress = w.progress if (w and running) else None
        ext = store.running_run()
        if not running and ext:
            running, progress = True, {"phase": ext.get("phase"), "message": "(started outside Mission Control)", "done": 0, "total": 0, "run_id": ext["id"]}
        last = [r for r in store.list_runs(limit=20) if r["status"] != "running" and not str(r.get("mode", "")).startswith("fault:")][:1]
        return {"agent": cfg.agent.__dict__, "running": running, "progress": progress, "last_run": last[0] if last else None,
                "pending_approvals": len(store.list_approvals("pending")), "budget": {**store.month_budget(), **{f"cap_{k}": v for k, v in cfg.limits.__dict__.items()}},
                "model": {"backend": cfg.model.backend, "name": cfg.model.ollama_model if cfg.model.backend == "ollama" else cfg.model.claude_model},
                "tools": cfg.tools_allowed, "approval_actions": cfg.approval_actions, "auth_required": bool(token),
                "tasks": [{k: v for k, v in t.items() if k != "body"} for t in brain.list_tasks(cfg)],
                "panels": [{"name": p["name"], "collection": p["collection"], "columns": p.get("columns", []), "actions": p.get("actions", [])} for p in panels],
                "collections": {c: len(store.list(c)) for c in store.collections()}, "ledger": ledger.verify()}

    @app.get("/api/runs")
    def runs(limit: int = 50):
        return store.list_runs(limit=limit)

    @app.get("/api/runs/{run_id}")
    def run_detail(run_id: str):
        r = store.get_run(run_id)
        if not r:
            raise HTTPException(404)
        r["activity"] = ledger.read(limit=500, run_id=run_id)
        return r

    @app.post("/api/run", dependencies=[Depends(require_token)])
    def start_run(req: RunRequest):
        if state["thread"] and state["thread"].is_alive():
            raise HTTPException(409, "a run is already in progress")
        w = worker_cls(cfg, store, ledger)
        t = threading.Thread(target=w.run, kwargs={"task": req.task, "input_text": req.input}, daemon=True)
        state.update({"worker": w, "thread": t})
        t.start()
        ledger.append("run_requested", None, source="mission_control", task=req.task)
        return {"started": True}

    @app.post("/api/run/stop", dependencies=[Depends(require_token)])
    def stop_run():
        w = state["worker"]
        if not w or not (state["thread"] and state["thread"].is_alive()):
            raise HTTPException(409, "no run in progress")
        w.stop()
        return {"stopping": True}

    @app.get("/api/run/progress")
    def progress():
        w = state["worker"]
        return {"running": bool(state["thread"] and state["thread"].is_alive()), "progress": w.progress if w else None}

    @app.get("/api/tasks")
    def tasks():
        return brain.list_tasks(cfg)

    @app.get("/api/tools")
    def tools():
        return [{"name": t.name, "description": t.description, "args": t.args, "risk": t.risk, "approval_action": t.approval_action}
                for t in allowed_tools(cfg).values()] + [{"name": n, "available": False} for n in cfg.tools_allowed if n not in REGISTRY]

    @app.get("/api/approvals")
    def list_approvals(status: str | None = None):
        rows = store.list_approvals(status)
        for a in rows:
            a["description"] = approvals.describe(a["action"], a["target"], a.get("payload"))
        return rows

    @app.post("/api/approvals/{aid}/approve", dependencies=[Depends(require_token)])
    def approve(aid: int, execute: bool = True):
        try:
            a = approvals.decide(store, ledger, aid, True, who="mission_control")
            return approvals.execute(cfg, store, ledger, aid) if execute else {"approval": a}
        except (KeyError, ValueError) as e:
            raise HTTPException(400, str(e))

    @app.post("/api/approvals/{aid}/deny", dependencies=[Depends(require_token)])
    def deny(aid: int):
        try:
            return approvals.decide(store, ledger, aid, False, who="mission_control")
        except (KeyError, ValueError) as e:
            raise HTTPException(400, str(e))

    @app.get("/api/activity")
    def activity(limit: int = 200, run: str | None = None):
        return ledger.read(limit=limit, run_id=run)

    @app.get("/api/activity/verify")
    def verify():
        return ledger.verify()

    @app.get("/api/brain")
    def brain_index():
        core = brain.read_core(cfg)
        return {"files": {k: {"path": str(cfg.core_files[k]), "chars": len(v)} for k, v in core.items()}, "skills": brain.list_skills(cfg)}

    @app.get("/api/brain/{name}")
    def brain_file(name: str):
        if name not in cfg.core_files:
            raise HTTPException(404)
        p = cfg.core_files[name]
        return {"name": name, "path": str(p), "content": p.read_text(encoding="utf-8") if p.exists() else ""}

    @app.put("/api/brain/{name}", dependencies=[Depends(require_token)])
    def brain_write(name: str, req: BrainFile):
        if name not in cfg.core_files:
            raise HTTPException(404)
        cfg.core_files[name].write_text(req.content, encoding="utf-8")
        ledger.append("brain_edited", None, file=name, chars=len(req.content), by="mission_control")
        return {"ok": True}

    @app.get("/api/skills/{name}")
    def skill(name: str):
        text = brain.read_skill(cfg, name)
        if not text:
            raise HTTPException(404)
        return {"name": name, "content": text}

    @app.get("/api/schedule")
    def sched():
        return schedule.status(cfg)

    @app.post("/api/schedule/install", dependencies=[Depends(require_token)])
    def sched_install(req: ScheduleRequest):
        res = schedule.install(cfg, req.time)
        ledger.append("schedule_installed", None, ok=res.get("ok"), time=req.time or cfg.schedule_time)
        return res

    @app.post("/api/schedule/remove", dependencies=[Depends(require_token)])
    def sched_remove():
        res = schedule.remove(cfg)
        ledger.append("schedule_removed", None, ok=res.get("ok"))
        return res

    @app.post("/api/schedule/run", dependencies=[Depends(require_token)])
    def sched_run():
        return schedule.run_now(cfg)

    @app.get("/api/doctor")
    def doc():
        return doctor.summarize(doctor.run_checks(cfg))

    @app.get("/api/openshell")
    def openshell_policy():
        from . import openshell
        pol = openshell.policy_for(cfg)
        return {"policy": pol, "yaml": openshell.to_yaml(pol), "launch": openshell.launch_doc(cfg)}

    @app.get("/api/health")
    def health():
        from .health import health_report
        return health_report(cfg)

    @app.get("/api/evals")
    def evals_last():
        from .evals import list_evals
        rows = store.list("evals", limit=10)
        return {"defined": list_evals(cfg), "runs": rows}

    @app.post("/api/evals/run", dependencies=[Depends(require_token)])
    def evals_run():
        if state["thread"] and state["thread"].is_alive():
            raise HTTPException(409, "a run is already in progress")
        from .evals import run_evals
        holder: dict = {}

        def go():
            holder["result"] = run_evals(cfg, worker_cls)
        t = threading.Thread(target=go, daemon=True)
        w = worker_cls(cfg, store, ledger)
        w.progress = {"phase": "EVALS", "message": "running evals/*.md", "done": 0, "total": 0, "run_id": None}
        state.update({"worker": w, "thread": t})
        t.start()
        ledger.append("evals_requested", None, source="mission_control")
        return {"started": True}

    @app.get("/api/faults")
    def faults_last():
        from .faults import scenarios
        return {"scenarios": scenarios(), "last": store.get("faults", "latest")}

    @app.post("/api/faults/run", dependencies=[Depends(require_token)])
    def faults_run():
        if state["thread"] and state["thread"].is_alive():
            raise HTTPException(409, "a run is already in progress")
        from .faults import run_faults
        t = threading.Thread(target=lambda: run_faults(cfg, worker_cls), daemon=True)
        w = worker_cls(cfg, store, ledger)
        w.progress = {"phase": "FAULTS", "message": "fault injection scenarios", "done": 0, "total": 0, "run_id": None}
        state.update({"worker": w, "thread": t})
        t.start()
        ledger.append("faults_requested", None, source="mission_control")
        return {"started": True}

    @app.get("/api/report/latest")
    def latest_report():
        for r in store.list_runs(limit=20):
            if r.get("report_path") and Path(r["report_path"]).exists():
                return {"run_id": r["id"], "path": r["report_path"], "content": Path(r["report_path"]).read_text(encoding="utf-8")}
        return {"run_id": None, "path": None, "content": None}

    @app.post("/api/chat", dependencies=[Depends(require_token)])
    def chat(req: ChatRequest):
        model = ModelClient(cfg, store)
        try:
            answer = chat_answer(cfg, store, model, req.question)
        except BudgetExceeded as e:
            raise HTTPException(429, str(e))
        except ModelError as e:
            raise HTTPException(502, str(e))
        ledger.append("chat", None, question=req.question[:200], model=model.name)
        return {"answer": answer, "usage": model.usage()}

    # ---- documents (agent-specific panels)
    @app.get("/api/docs/{collection}")
    def docs_list(collection: str, limit: int = Query(500, le=5000)):
        return store.list(collection, limit=limit)

    @app.get("/api/docs/{collection}/{doc_id}")
    def docs_get(collection: str, doc_id: str):
        d = store.get(collection, doc_id)
        if not d:
            raise HTTPException(404)
        return d

    @app.put("/api/docs/{collection}/{doc_id}", dependencies=[Depends(require_token)])
    def docs_put(collection: str, doc_id: str, req: DocRequest):
        old = store.get(collection, doc_id) or {}
        merged = {k: v for k, v in old.items() if not k.startswith("_") and k != "id"}
        merged.update(req.fields)
        store.put(collection, doc_id, merged)
        ledger.append("doc_edited", None, collection=collection, id=doc_id, by="mission_control", fields=list(req.fields))
        return store.get(collection, doc_id)

    @app.delete("/api/docs/{collection}/{doc_id}", dependencies=[Depends(require_token)])
    def docs_delete(collection: str, doc_id: str):
        store.delete(collection, doc_id)
        ledger.append("doc_deleted", None, collection=collection, id=doc_id, by="mission_control")
        return {"ok": True}

    # ---- panel actions registered by the agent package: {name, collection, actions:[{label, action_id}]}; handler(cfg, store, ledger, doc_id, action_id)
    @app.post("/api/panel/{collection}/{doc_id}/{action_id}", dependencies=[Depends(require_token)])
    def panel_action(collection: str, doc_id: str, action_id: str):
        for p in panels:
            if p["collection"] == collection and p.get("handler"):
                try:
                    return p["handler"](cfg, store, ledger, doc_id, action_id)
                except (KeyError, ValueError) as e:
                    raise HTTPException(400, str(e))
        raise HTTPException(404, "no handler for this panel action")

    return app
