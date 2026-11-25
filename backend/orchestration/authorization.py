"""Operator authorisations for controller actions that change authority: objective approval, objective
amendment and a new attempt after cancellation. Ed25519-signed by the operator key (outside the repository);
the controller holds only the public key. Stage 02 replaces this with authenticated human approvals."""
from __future__ import annotations

import base64
import time
from dataclasses import dataclass
from pathlib import Path

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

from backend.contracts import canonical_json


class AuthorizationError(PermissionError):
    pass


@dataclass(frozen=True)
class OperatorAuthorization:
    action: str               # approve_objective | amend_objective | reattempt_task
    subject: str              # objective id or "objective_id/TASK-0001"
    payload_hash: str         # hash of exactly what is authorised
    operator: str
    expires_at: float
    signature: str

    def message(self) -> bytes:
        return canonical_json([self.action, self.subject, self.payload_hash, self.operator, self.expires_at]).encode()


def sign_authorization(private_key_path: Path, action: str, subject: str, payload_hash: str, operator: str,
                       ttl_s: float = 600) -> OperatorAuthorization:
    key = serialization.load_pem_private_key(private_key_path.read_bytes(), password=None)
    assert isinstance(key, Ed25519PrivateKey)
    a = OperatorAuthorization(action, subject, payload_hash, operator, round(time.time() + ttl_s, 3), "")
    return OperatorAuthorization(**{**a.__dict__, "signature": base64.b64encode(key.sign(a.message())).decode()})


class AuthorizationVerifier:
    def __init__(self, public_key_path: Path):
        self.public_key_path = public_key_path

    def verify(self, auth: OperatorAuthorization | None, action: str, subject: str, payload_hash: str) -> str:
        if auth is None:
            raise AuthorizationError(f"{action} requires an operator authorisation")
        if (auth.action, auth.subject, auth.payload_hash) != (action, subject, payload_hash):
            raise AuthorizationError("authorisation does not match this exact action, subject and payload")
        if auth.expires_at < time.time():
            raise AuthorizationError("authorisation expired")
        if not self.public_key_path.is_file():
            raise AuthorizationError(f"operator public key {self.public_key_path} not installed")
        pub = serialization.load_pem_public_key(self.public_key_path.read_bytes())
        if not isinstance(pub, Ed25519PublicKey):
            raise AuthorizationError("operator public key is not Ed25519")
        try:
            pub.verify(base64.b64decode(auth.signature), auth.message())
        except (InvalidSignature, ValueError) as exc:
            raise AuthorizationError("authorisation signature invalid") from exc
        return auth.operator
