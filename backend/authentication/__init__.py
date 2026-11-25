"""Authentication boundary. Stage 01A: there are no user accounts yet (Stage 02, BLOCKERS B-001) — every API
caller is ANONYMOUS and presented credentials are ignored. The only authenticated principal is the runtime
controller, attested by `attestation.Attestor`; the policy engine rejects any principal it did not sign."""
from __future__ import annotations

from typing import Mapping, Protocol

from backend.contracts import Principal

from .attestation import Attestor, get_attestor


class Authenticator(Protocol):
    def authenticate(self, headers: Mapping[str, str]) -> Principal: ...


class Stage01Authenticator:
    """Returns the anonymous principal for every request. A presented bearer token does NOT authenticate."""

    def authenticate(self, headers: Mapping[str, str]) -> Principal:
        return Principal.anonymous()


def get_authenticator() -> Authenticator:
    return Stage01Authenticator()


__all__ = ["Attestor", "get_attestor", "Authenticator", "Stage01Authenticator", "get_authenticator"]
