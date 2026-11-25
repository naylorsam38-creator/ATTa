"""Credential REFERENCES (Stage 12 §27, §28). Stage 02 owns the credential store and its identity binding.

An integration declares credential requirements (name, kind, description); the platform derives an opaque reference
credref://<integration_id>/<name>. Raw values never appear in manifests, skill files, prompts, logs, audit output,
frontend state or test evidence. Resolution happens only through a CredentialBroker, which:
  * resolves only references the APPROVED manifest declared (undeclared requests are refused and audited);
  * offers no enumeration — there is no 'list secrets' operation by design;
  * in this sidecar is the UnavailableCredentialBroker: every credential-dependent integration reports
    AUTH_REQUIRED and is BLOCKED from execution until Stage 02 provides a real broker."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from .content import CREDENTIAL_REF


class CredentialError(PermissionError):
    pass


def credential_ref(integration_id: str, name: str) -> str:
    ref = f"credref://{integration_id}/{name}"
    if not CREDENTIAL_REF.match(ref):
        raise CredentialError(f"invalid credential reference {ref!r}")
    return ref


@dataclass(frozen=True)
class CredentialStatus:
    ref: str
    available: bool
    reason: str


class CredentialBroker(Protocol):
    name: str

    def status(self, ref: str) -> CredentialStatus: ...
    def resolve(self, ref: str, *, integration_id: str, declared: frozenset[str], purpose: str) -> str: ...


class UnavailableCredentialBroker:
    """Default until Stage 02. Never returns a value; never claims availability."""
    name = "unavailable (Stage 02 credential management not merged)"

    def status(self, ref: str) -> CredentialStatus:
        return CredentialStatus(ref, False, "credential management is provided by Stage 02 (BLOCKED)")

    def resolve(self, ref: str, *, integration_id: str, declared: frozenset[str], purpose: str) -> str:
        check_declared(ref, integration_id, declared)
        raise CredentialError("credential management is provided by Stage 02 (BLOCKED); no credential resolved")


def check_declared(ref: str, integration_id: str, declared: frozenset[str]) -> None:
    """Refuse anything outside the integration's approved, declared requirements (prevents secret enumeration
    and cross-integration access)."""
    if not CREDENTIAL_REF.match(ref or ""):
        raise CredentialError("not a credential reference")
    owner, name = ref[len("credref://"):].split("/", 1)
    if owner != integration_id:
        raise CredentialError(f"integration '{integration_id}' may not request another integration's credentials")
    if name not in declared:
        raise CredentialError(f"credential '{name}' was not declared in the approved manifest; a new validation and "
                              "approval is required")
