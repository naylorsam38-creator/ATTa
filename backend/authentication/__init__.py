"""Authentication boundary. Stage 01 provides the interface only: every caller is ANONYMOUS and carries no
permissions. Credentials presented in requests are ignored (never trusted) until Stage 02 implements
verified authentication. Status: BLOCKED on Stage 02 (see BLOCKERS.md B-001)."""
from __future__ import annotations

from typing import Mapping, Protocol

from backend.contracts import Principal


class Authenticator(Protocol):
    def authenticate(self, headers: Mapping[str, str]) -> Principal: ...


class Stage01Authenticator:
    """Returns the anonymous principal for every request. A presented bearer token does NOT authenticate."""

    def authenticate(self, headers: Mapping[str, str]) -> Principal:
        return Principal.anonymous()


def get_authenticator() -> Authenticator:
    return Stage01Authenticator()
