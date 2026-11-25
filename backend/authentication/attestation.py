"""Principal attestation: the authenticator signs every principal it issues with a per-process random key.
The policy engine verifies the signature, so authority cannot be forged by constructing a Principal object."""
from __future__ import annotations

import hashlib
import hmac
import os
import threading
import time

from backend.contracts import Principal, canonical_json

ATTESTATION_TTL_S = 300.0


def _payload(p: Principal) -> bytes:
    return canonical_json({"id": p.id, "kind": p.kind.value, "tenant": p.tenant_id, "role": p.role,
                           "session": p.session_id, "permissions": sorted(p.permissions),
                           "authenticated": p.authenticated, "expires_at": p.expires_at}).encode()


class Attestor:
    def __init__(self, key: bytes | None = None):
        self._key = key or os.urandom(32)

    def issue(self, **fields) -> Principal:
        fields.setdefault("expires_at", time.time() + ATTESTATION_TTL_S)
        unsigned = Principal(**fields)
        return unsigned.model_copy(update={"attestation": self._sign(unsigned)})

    def _sign(self, p: Principal) -> str:
        return hmac.new(self._key, _payload(p), hashlib.sha256).hexdigest()

    def sign_fields(self, *fields) -> str:
        return hmac.new(self._key, canonical_json([str(f) for f in fields]).encode(), hashlib.sha256).hexdigest()

    def verify_fields(self, signature: str, *fields) -> bool:
        return hmac.compare_digest(self.sign_fields(*fields), signature or "")

    def verify(self, p: Principal) -> bool:
        if not p.attestation or p.expires_at is None or p.expires_at < time.time():
            return False
        return hmac.compare_digest(self._sign(p), p.attestation)


_attestor: Attestor | None = None
_lock = threading.Lock()


def get_attestor() -> Attestor:
    global _attestor
    with _lock:
        if _attestor is None:
            _attestor = Attestor()
        return _attestor
