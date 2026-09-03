"""Agent Recall: when a run, model, task, skill, record or approval turns out to be bad, find everything derived from it,
quarantine those artifacts, deny approvals that depend on them, and publish a signed advisory (with a lift path).

Revocation standards stop at "the action was documented"; this layer answers what happened to the WORK. It is generic
provenance reachability over the ledger and record store (every record carries the run that created/updated it; records
reference each other by *_id fields; approvals point at runs and targets). Deliberately simple graph math: reach(seed).
Runs contaminate the records they created; id-linked records contaminate each other (conservative on purpose: the owner previews
the impact before issuing, and lift restores); records contaminate the approvals that target them."""
from __future__ import annotations

import json
import secrets
from collections import deque
from datetime import datetime, timezone

from . import brain
from .config import Config
from .keys import KeyStore, canonical
from .ledger import Ledger
from .store import Store

SEED_TYPES = ("run", "record", "approval", "model", "task", "skill")
RECORD_STATUS_FIELDS = ("status",)


def _rid(collection: str, doc_id: str) -> str:
    return f"record:{collection}/{doc_id}"


def provenance_graph(store: Store) -> dict:
    """nodes: run:<id>, record:<coll>/<id>, approval:<id>; edges parent -> child (child derived from parent)."""
    edges: dict[str, set[str]] = {}
    nodes: dict[str, dict] = {}

    def add(a: str, b: str) -> None:
        edges.setdefault(a, set()).add(b)
        edges.setdefault(b, set())

    for r in store.list_runs(limit=5000):
        nodes[f"run:{r['id']}"] = {"kind": "run", "mode": r.get("mode"), "status": r.get("status"), "model": (r.get("receipt") or {}).get("model") if isinstance(r.get("receipt"), dict) else None}
        edges.setdefault(f"run:{r['id']}", set())
    all_records: dict[str, dict] = {}
    for coll in store.collections():
        if coll in ("evidence", "evals", "advisories", "faults"):
            continue
        for d in store.list(coll, limit=5000):
            key = _rid(coll, d["id"])
            all_records[key] = d
            nodes[key] = {"kind": "record", "collection": coll, "status": d.get("status")}
            for f in ("created_by_run", "updated_by_run", "run_id"):
                if d.get(f):
                    add(f"run:{d[f]}", key)
    id_index = {d["id"]: k for k, d in all_records.items()}
    for key, d in all_records.items():
        for f, v in d.items():
            if f.endswith("_id") and isinstance(v, str) and v in id_index and id_index[v] != key:
                add(id_index[v], key)  # the referenced record informed this one...
                add(key, id_index[v])  # ...and, conservatively, vice versa: id-linked records are one unit of work (over-recall + lift beats under-recall)
    for a in store.list_approvals():
        key = f"approval:{a['id']}"
        nodes[key] = {"kind": "approval", "action": a["action"], "target": a["target"], "status": a["status"]}
        if a.get("run_id"):
            add(f"run:{a['run_id']}", key)
        if a.get("target") in id_index:
            add(id_index[a["target"]], key)
        elif a.get("finding_id") and a["finding_id"] in id_index:
            add(id_index[a["finding_id"]], key)
    return {"nodes": nodes, "edges": {k: sorted(v) for k, v in edges.items()}}


def seeds_for(cfg: Config, store: Store, ledger: Ledger, seed_type: str, seed: str) -> list[str]:
    if seed_type == "run":
        return [f"run:{seed}"]
    if seed_type == "record":
        coll, _, doc_id = seed.partition("/")
        return [_rid(coll, doc_id)]
    if seed_type == "approval":
        return [f"approval:{seed}"]
    if seed_type == "model":
        return [f"run:{r['id']}" for r in store.list_runs(limit=5000) if isinstance(r.get("receipt"), dict) and (r["receipt"].get("model") or "").endswith(seed)]
    if seed_type == "task":
        return [f"run:{r['id']}" for r in store.list_runs(limit=5000) if seed in (r.get("mode") or "") or seed in ((r.get("receipt") or {}).get("tasks") or [])]
    if seed_type == "skill":
        tasks = {t["name"] for t in brain.list_tasks(cfg) if seed in (t.get("skills") or [])}
        return [f"run:{r['id']}" for r in store.list_runs(limit=5000) if any(t in (r.get("mode") or "") or t in ((r.get("receipt") or {}).get("tasks") or []) for t in tasks)]
    raise ValueError(f"seed_type must be one of {SEED_TYPES}")


def impact(cfg: Config, store: Store, ledger: Ledger, seed_type: str, seed: str) -> dict:
    g = provenance_graph(store)
    seeds = [s for s in seeds_for(cfg, store, ledger, seed_type, seed) if s in g["nodes"]]
    seen: dict[str, int] = {}
    q = deque((s, 0) for s in seeds)
    while q:
        n, hops = q.popleft()
        if n in seen:
            continue
        seen[n] = hops
        for child in g["edges"].get(n, []):
            if child not in seen:
                q.append((child, hops + 1))
    affected = [{"node": n, "hops": h, **g["nodes"][n]} for n, h in sorted(seen.items(), key=lambda kv: (kv[1], kv[0]))]
    return {"seed_type": seed_type, "seed": seed, "seeds_found": seeds, "affected": affected,
            "counts": {"runs": sum(1 for a in affected if a["kind"] == "run"), "records": sum(1 for a in affected if a["kind"] == "record"),
                       "approvals": sum(1 for a in affected if a["kind"] == "approval")}}


def recall(cfg: Config, store: Store, ledger: Ledger, seed_type: str, seed: str, reason: str, actor: str = "owner") -> dict:
    imp = impact(cfg, store, ledger, seed_type, seed)
    keys = KeyStore(cfg)
    adv_id = "adv-" + datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S") + "-" + secrets.token_hex(2)
    quarantined, denied = [], []
    for a in imp["affected"]:
        if a["kind"] == "record":
            coll, doc_id = a["collection"], a["node"].split("/", 1)[1]
            d = store.get(coll, doc_id) or {}
            base = {k: v for k, v in d.items() if not k.startswith("_") and k != "id"}
            if base.get("status") == "recalled":
                continue
            base.update({"recall_prior_status": base.get("status"), "status": "recalled", "recalled_by": adv_id, "recall_reason": reason[:300]})
            store.put(coll, doc_id, base)
            quarantined.append(a["node"])
        elif a["kind"] == "approval" and a["status"] in ("pending", "approved"):
            aid = int(a["node"].split(":")[1])
            store.record_execution(aid, "denied", f"recalled by {adv_id}: {reason[:200]}")
            denied.append(aid)
    head = ledger.read(limit=1)
    advisory = {"id": adv_id, "agent": cfg.agent.slug, "issuer": keys.did, "issued_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "status": "issued", "seed_type": seed_type, "seed": seed, "reason": reason[:500], "actor": actor,
                "affected": imp["affected"], "quarantined_records": quarantined, "denied_approvals": denied,
                "ledger_head": head[0]["hash"] if head else None}
    signed = {"advisory": advisory, "alg": "Ed25519", "public_key": keys.identity_public_b64, "signature": keys.sign(canonical(advisory))}
    d = cfg.data_dir / "advisories"
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{adv_id}.json").write_text(json.dumps(signed, indent=2, default=str), encoding="utf-8")
    store.put("advisories", adv_id, {**{k: v for k, v in advisory.items() if k != "affected"}, "affected_count": len(imp["affected"]), "path": str(d / f"{adv_id}.json")})
    ledger.append("recall_issued", None, advisory=adv_id, seed_type=seed_type, seed=seed, quarantined=len(quarantined), denied=len(denied), by=actor)
    return {**signed, "counts": imp["counts"]}


def lift(cfg: Config, store: Store, ledger: Ledger, advisory_id: str, reason: str, actor: str = "owner") -> dict:
    adv = store.get("advisories", advisory_id)
    if not adv:
        raise KeyError(advisory_id)
    if adv.get("status") != "issued":
        raise ValueError(f"advisory {advisory_id} is {adv.get('status')}")
    restored = []
    for node in adv.get("quarantined_records") or []:
        coll, doc_id = node.split(":", 1)[1].split("/", 1)
        d = store.get(coll, doc_id) or {}
        base = {k: v for k, v in d.items() if not k.startswith("_") and k != "id"}
        if base.get("recalled_by") == advisory_id:
            base["status"] = base.pop("recall_prior_status", "open")
            base.pop("recalled_by", None)
            base.pop("recall_reason", None)
            store.put(coll, doc_id, base)
            restored.append(node)
    keys = KeyStore(cfg)
    lift_rec = {"advisory": advisory_id, "lifted_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "reason": reason[:300], "actor": actor, "restored": restored}
    store.put("advisories", advisory_id, {**{k: v for k, v in adv.items() if not k.startswith("_") and k != "id"}, "status": "lifted", "lift": lift_rec,
                                          "lift_signature": keys.sign(canonical(lift_rec))})
    ledger.append("recall_lifted", None, advisory=advisory_id, restored=len(restored), by=actor)
    return {"advisory": advisory_id, "restored": restored, "note": "denied approvals stay denied; re-propose if still wanted"}


def verify_advisory(path) -> dict:
    signed = json.loads(open(path, encoding="utf-8").read())
    ok = KeyStore.verify(signed.get("public_key", ""), canonical(signed.get("advisory") or {}), signed.get("signature", ""))
    return {"ok": ok, "advisory": signed.get("advisory", {}).get("id"), "issuer": signed.get("advisory", {}).get("issuer")}
