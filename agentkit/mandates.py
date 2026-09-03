"""Approvals as signed mandates. When the owner clicks Approve in Mission Control (the "Trusted Surface"), the approval is
issued as an SD-JWT signed with the agent's P-256 key (ES256), shaped after Google's AP2 mandates: selectively-disclosable
claims, a `cnf` key binding, `iat`/`exp`, and for quotes an open Payment-Mandate-style constraint set (amount range, allowed
payees). Scope, honestly stated: AP2-shaped, verifiable offline with the agent's JWK, NOT yet interoperability-tested against a
real Credential Provider or network. It turns "a human approved this" from a log line into a portable cryptographic proof."""
from __future__ import annotations

import hashlib
import json
import secrets
import time

from .config import Config
from .keys import KeyStore, b64u, b64u_dec, canonical

APPROVAL_VCT = "mandate.approval.1"
PAYMENT_OPEN_VCT = "mandate.payment.open.1"


def _disclosure(name: str, value) -> str:
    return b64u(json.dumps([secrets.token_urlsafe(12), name, value], separators=(",", ":"), ensure_ascii=False, default=str).encode())


def _digest(disclosure: str) -> str:
    return b64u(hashlib.sha256(disclosure.encode()).digest())


def _jwt(keys: KeyStore, header: dict, payload: dict) -> str:
    h, p = b64u(canonical(header)), b64u(canonical(payload))
    return f"{h}.{p}.{keys.sign_es256(f'{h}.{p}'.encode())}"


def issue_approval_mandate(cfg: Config, approval: dict, approver: str = "owner", ttl_s: int = 3600) -> str:
    """SD-JWT: issuer-signed JWT (hidden claims as digests) ~ disclosures ~ . Returns the compact serialization."""
    keys = KeyStore(cfg)
    now = int(time.time())
    payload_hash = hashlib.sha256(canonical(approval.get("payload") or {})).hexdigest()
    disclosures = [_disclosure("action", approval["action"]), _disclosure("target", approval["target"]),
                   _disclosure("payload_sha256", payload_hash), _disclosure("approver", approver),
                   _disclosure("rationale", (approval.get("rationale") or "")[:300]), _disclosure("approval_id", approval["id"])]
    payload = {"iss": keys.did, "vct": APPROVAL_VCT, "iat": now, "exp": now + ttl_s, "_sd_alg": "sha-256",
               "_sd": sorted(_digest(d) for d in disclosures), "cnf": {"jwk": {k: v for k, v in keys.p256_jwk.items() if k != "kid"}},
               "agent": cfg.agent.slug, "run_id": approval.get("run_id")}
    jwt = _jwt(keys, {"alg": "ES256", "typ": "mandate+sd-jwt", "kid": keys.p256_jwk["kid"]}, payload)
    return jwt + "~" + "~".join(disclosures) + "~"


def issue_payment_open_mandate(cfg: Config, payee: dict, max_amount_minor: int, currency: str = "USD", ttl_s: int = 86400) -> str:
    """AP2-shaped OPEN Payment Mandate: constrains any later closed mandate to a payee set and an amount range."""
    keys = KeyStore(cfg)
    now = int(time.time())
    constraints = [{"type": "payment.amount_range", "currency": currency, "min": 0, "max": int(max_amount_minor)},
                   {"type": "payment.allowed_payees", "allowed": [payee]}]
    disclosures = [_disclosure("constraints", constraints), _disclosure("issued_by", "mission-control-approval")]
    payload = {"iss": keys.did, "vct": PAYMENT_OPEN_VCT, "iat": now, "exp": now + ttl_s, "_sd_alg": "sha-256",
               "_sd": sorted(_digest(d) for d in disclosures), "cnf": {"jwk": {k: v for k, v in keys.p256_jwk.items() if k != "kid"}}}
    return _jwt(keys, {"alg": "ES256", "typ": "mandate+sd-jwt", "kid": keys.p256_jwk["kid"]}, payload) + "~" + "~".join(disclosures) + "~"


def verify_mandate(sd_jwt: str, jwk: dict | None = None) -> dict:
    """Verify signature (with the given JWK, else the cnf key inside), digests, and expiry. Returns disclosed claims."""
    try:
        parts = sd_jwt.split("~")
        jwt, disclosures = parts[0], [p for p in parts[1:] if p]
        h, p, s = jwt.split(".")
        header, payload = json.loads(b64u_dec(h)), json.loads(b64u_dec(p))
        key = jwk or payload.get("cnf", {}).get("jwk")
        if header.get("alg") != "ES256" or not KeyStore.verify_es256(key, f"{h}.{p}".encode(), s):
            return {"ok": False, "reason": "signature invalid"}
        if payload.get("exp") and payload["exp"] < time.time():
            return {"ok": False, "reason": "expired", "payload": payload}
        claims = {}
        for d in disclosures:
            if _digest(d) not in payload.get("_sd", []):
                return {"ok": False, "reason": "disclosure digest not in _sd"}
            _, name, value = json.loads(b64u_dec(d))
            claims[name] = value
        return {"ok": True, "issuer": payload.get("iss"), "vct": payload.get("vct"), "iat": payload.get("iat"), "exp": payload.get("exp"),
                "claims": claims, "kid": header.get("kid")}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "reason": f"malformed: {type(e).__name__}"}
