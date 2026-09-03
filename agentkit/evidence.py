"""Evidence bundle: everything a certifier, registry, insurer or buyer needs to trust this agent, produced by the agent
itself and signed with its identity key. Shape: an agent manifest (identity, authority, build provenance) plus the latest
verification artifacts (evals, faults, health, runtime policy) and a ledger checkpoint. Portable, hash-addressed, verifiable
offline with `verify_bundle`. Inspired by SBOM-style agent manifests; it is agentkit's own documented format, not a claim of
conformance to any third-party certification scheme."""
from __future__ import annotations

import hashlib
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path

from . import brain
from .config import Config
from .keys import KeyStore, canonical
from .ledger import Ledger
from .store import Store

FORMAT = "agentkit-evidence/1"


def _sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def _sha_text(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def manifest(cfg: Config, store: Store, ledger: Ledger, keys: KeyStore) -> dict:
    from .health import health_report
    core = brain.read_core(cfg)
    spec = cfg.root / "spec.json"
    pol = cfg.root / "openshell" / "policy.yaml"
    evals = store.list("evals", limit=1)
    faults = store.get("faults", "latest")
    health = health_report(cfg)
    lv = ledger.verify()
    last = ledger.read(limit=1)
    return {
        "format": FORMAT, "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "agent": {**cfg.agent.__dict__, **keys.public_record()},
        "authority": {"tools_allowed": list(cfg.tools_allowed), "approval_actions": list(cfg.approval_actions), "limits": cfg.limits.__dict__,
                      "model_backend": cfg.model.backend, "schedule_time": cfg.schedule_time},
        "build": {"spec_sha256": _sha(spec) if spec.exists() else None, "built_by": cfg.extra.get("built_by"),
                  "core_files": {k: _sha_text(v) for k, v in core.items()},
                  "skills": [{"name": s["name"], "sha256": _sha(cfg.root / s["path"])} for s in brain.list_skills(cfg)],
                  "tasks": [{"name": t["name"], "schedule": t["schedule"], "tools": t["tools"], "deliverable_items": len(t["deliverable"])} for t in brain.list_tasks(cfg)]},
        "runtime": {"openshell_policy_sha256": _sha(pol) if pol.exists() else None, "openshell_policy_path": str(pol.relative_to(cfg.root)) if pol.exists() else None},
        "verification": {
            "health": {"grade": health["grade"], "reasons": health["reasons"], "runs": health["runs"], "safety": health["safety"]},
            "evals": ({k: evals[0].get(k) for k in ("stamp", "verdict", "evals", "avg_outcome", "avg_tool_use", "avg_efficiency", "avg_safety", "avg_quality")} if evals else None),
            "faults": ({k: faults.get(k) for k in ("verdict", "passed", "total")} | {"scenarios": [{r["scenario"]: r["ok"]} for r in faults.get("results", [])]} if faults else None),
        },
        "ledger": {"ok": lv["ok"], "events": lv["count"], "head_hash": (last[0]["hash"] if last else None)},
    }


def build_bundle(cfg: Config, store: Store | None = None, ledger: Ledger | None = None) -> Path:
    store, ledger = store or Store(cfg.db), ledger or Ledger(cfg.ledger)
    keys = KeyStore(cfg)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    out = cfg.data_dir / "evidence" / stamp
    out.mkdir(parents=True, exist_ok=True)
    m = manifest(cfg, store, ledger, keys)
    (out / "manifest.json").write_text(json.dumps(m, indent=2, default=str), encoding="utf-8")
    # attached artifacts
    evals = store.list("evals", limit=1)
    if evals and evals[0].get("dir") and Path(evals[0]["dir"]).exists():
        for f in ("summary.json", "report.md", "scores.jsonl"):
            src = Path(evals[0]["dir"]) / f
            if src.exists():
                shutil.copy2(src, out / f"evals-{f}")
    faults = store.get("faults", "latest")
    if faults:
        (out / "faults.json").write_text(json.dumps(faults, indent=2, default=str), encoding="utf-8")
    pol = cfg.root / "openshell" / "policy.yaml"
    if pol.exists():
        shutil.copy2(pol, out / "openshell-policy.yaml")
    (out / "agent-manifest.yaml").write_text(_manifest_yaml(m), encoding="utf-8")
    files = {p.name: _sha(p) for p in sorted(out.iterdir()) if p.is_file() and p.name != "signature.json"}
    manifest_sha = _sha(out / "manifest.json")
    payload = {"format": FORMAT, "manifest_sha256": manifest_sha, "files": files, "signed_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
               "signer": keys.did}
    sig = {"alg": "Ed25519", "public_key": keys.identity_public_b64, "payload": payload, "signature": keys.sign(canonical(payload))}
    (out / "signature.json").write_text(json.dumps(sig, indent=2), encoding="utf-8")
    store.put("evidence", stamp, {"dir": str(out), "manifest_sha256": manifest_sha, "signer": keys.did, "health": m["verification"]["health"]["grade"],
                                  "evals": (m["verification"]["evals"] or {}).get("verdict"), "faults": (m["verification"]["faults"] or {}).get("verdict")})
    ledger.append("evidence_bundle", None, stamp=stamp, manifest_sha256=manifest_sha, files=len(files))
    return out


def verify_bundle(bundle_dir: Path) -> dict:
    d = Path(bundle_dir)
    reasons: list[str] = []
    sig_p = d / "signature.json"
    if not sig_p.exists():
        return {"ok": False, "reasons": ["signature.json missing"]}
    sig = json.loads(sig_p.read_text(encoding="utf-8"))
    payload = sig.get("payload") or {}
    if not KeyStore.verify(sig.get("public_key", ""), canonical(payload), sig.get("signature", "")):
        reasons.append("signature does not verify")
    for name, expected in (payload.get("files") or {}).items():
        p = d / name
        if not p.exists():
            reasons.append(f"missing {name}")
        elif _sha(p) != expected:
            reasons.append(f"hash mismatch {name}")
    if (d / "manifest.json").exists() and _sha(d / "manifest.json") != payload.get("manifest_sha256"):
        reasons.append("manifest hash mismatch")
    return {"ok": not reasons, "reasons": reasons, "signer": payload.get("signer"), "signed_at": payload.get("signed_at"), "files": len(payload.get("files") or {})}


def latest_bundle(cfg: Config, store: Store | None = None) -> dict | None:
    rows = (store or Store(cfg.db)).list("evidence", limit=1)
    return rows[0] if rows else None


def _manifest_yaml(m: dict) -> str:
    a, au, b, v = m["agent"], m["authority"], m["build"], m["verification"]
    L = [f"# Agent manifest — {a['name']} v{a['version']} — generated by agentkit ({m['format']})",
         "agent:", f"  name: {a['name']}", f"  slug: {a['slug']}", f"  version: {a['version']}", f"  organization: {a['organization']}",
         f"  did: {a['did']}", f"  identity_ed25519: {a['identity_ed25519']}", f"  responsibility: {json.dumps(a.get('responsibility', ''))}",
         "authority:", f"  tools_allowed: [{', '.join(au['tools_allowed'])}]", f"  approval_actions: [{', '.join(au['approval_actions'])}]",
         f"  model_backend: {au['model_backend']}", "  limits:"] + [f"    {k}: {v_}" for k, v_ in au["limits"].items()] + [
         "build:", f"  spec_sha256: {b['spec_sha256']}", f"  built_by: {b['built_by']}", "  skills:"] + [f"    - {{ name: {s['name']}, sha256: {s['sha256']} }}" for s in b["skills"]] + [
         "  tasks:"] + [f"    - {{ name: {t['name']}, schedule: {t['schedule']}, tools: [{', '.join(t['tools'])}] }}" for t in b["tasks"]] + [
         "runtime:", f"  openshell_policy_sha256: {m['runtime']['openshell_policy_sha256']}",
         "verification:", f"  health: {v['health']['grade']}", f"  evals: {json.dumps(v['evals'])}", f"  faults: {json.dumps(v['faults'])}",
         "ledger:", f"  ok: {m['ledger']['ok']}", f"  events: {m['ledger']['events']}", f"  head_hash: {m['ledger']['head_hash']}"]
    return "\n".join(L) + "\n"
