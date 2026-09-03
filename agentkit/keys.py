"""Per-agent key store: an Ed25519 identity key (signs evidence bundles, advisories, ledger checkpoints) and a P-256 key
(signs AP2-shaped SD-JWT mandates, which require ES256). Keys are generated on first use under data/keys/, never leave the box,
and are excluded from evidence bundles and state syncs unless the owner opts in."""
from __future__ import annotations

import base64
import hashlib
import json
import os
from pathlib import Path

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, ed25519
from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature, encode_dss_signature

from .config import Config


def b64u(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def b64u_dec(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


class KeyStore:
    def __init__(self, cfg: Config):
        self.dir = cfg.data_dir / "keys"
        self.dir.mkdir(parents=True, exist_ok=True)
        self.slug = cfg.agent.slug
        self._ed = self._load_or_create(self.dir / "identity_ed25519.pem", ed25519.Ed25519PrivateKey.generate)
        self._p256 = self._load_or_create(self.dir / "mandate_p256.pem", lambda: ec.generate_private_key(ec.SECP256R1()))

    def _load_or_create(self, path: Path, gen):
        if path.exists():
            return serialization.load_pem_private_key(path.read_bytes(), password=None)
        key = gen()
        pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
        path.write_bytes(pem)
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass
        return key

    # ---- identity (Ed25519)
    @property
    def identity_public_b64(self) -> str:
        return b64u(self._ed.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw))

    @property
    def did(self) -> str:
        """A stable, self-certifying identifier derived from the identity public key (did:key-style, not a registered method)."""
        return f"did:agentkit:{self.slug}:{hashlib.sha256(self.identity_public_b64.encode()).hexdigest()[:16]}"

    def sign(self, data: bytes) -> str:
        return b64u(self._ed.sign(data))

    @staticmethod
    def verify(public_b64: str, data: bytes, signature_b64: str) -> bool:
        try:
            ed25519.Ed25519PublicKey.from_public_bytes(b64u_dec(public_b64)).verify(b64u_dec(signature_b64), data)
            return True
        except Exception:  # noqa: BLE001
            return False

    # ---- mandates (P-256 / ES256)
    @property
    def p256_jwk(self) -> dict:
        nums = self._p256.public_key().public_numbers()
        return {"kty": "EC", "crv": "P-256", "x": b64u(nums.x.to_bytes(32, "big")), "y": b64u(nums.y.to_bytes(32, "big")),
                "kid": f"{self.slug}-mandate-key-1"}

    def sign_es256(self, data: bytes) -> str:
        der = self._p256.sign(data, ec.ECDSA(hashes.SHA256()))
        r, s = decode_dss_signature(der)
        return b64u(r.to_bytes(32, "big") + s.to_bytes(32, "big"))

    @staticmethod
    def verify_es256(jwk: dict, data: bytes, signature_b64: str) -> bool:
        try:
            x, y = int.from_bytes(b64u_dec(jwk["x"]), "big"), int.from_bytes(b64u_dec(jwk["y"]), "big")
            pub = ec.EllipticCurvePublicNumbers(x, y, ec.SECP256R1()).public_key()
            raw = b64u_dec(signature_b64)
            der = encode_dss_signature(int.from_bytes(raw[:32], "big"), int.from_bytes(raw[32:], "big"))
            pub.verify(der, data, ec.ECDSA(hashes.SHA256()))
            return True
        except Exception:  # noqa: BLE001
            return False

    def public_record(self) -> dict:
        return {"did": self.did, "identity_ed25519": self.identity_public_b64, "mandate_p256_jwk": self.p256_jwk}


def canonical(obj) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str).encode()
