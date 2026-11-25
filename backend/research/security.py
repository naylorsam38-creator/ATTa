"""Stage 02 security contract for research operations (§29) — an INTERFACE, not a competing auth system.

Stage 02 will supply the authenticated principal, tenant, network permission, source restrictions and approval
requirements. Until then every protected research operation stays constrained by the Stage 01A policy:
  * the /v1 research API is evaluated by the central policy engine, which DENIES it (no authentication yet);
  * research executes only after an operator Ed25519 approval of its Stage 01A objective;
  * tool execution happens only as the attested runtime controller, LOW risk, granted permissions only."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable


@dataclass(frozen=True)
class ResearchSecurityContext:
    principal_id: str | None              # Stage 02 authenticated principal (None until Stage 02)
    tenant_id: str | None                 # Stage 02 tenant (RLS)
    network_permitted: bool               # may this principal's research reach external networks?
    source_restrictions: tuple[str, ...] = field(default_factory=tuple)   # extra excluded domains (tenant policy)
    approval_requirements: tuple[str, ...] = field(default_factory=tuple)
    authority: str = "stage01a-operator"  # who authorises execution until Stage 02


@runtime_checkable
class ResearchSecurityProvider(Protocol):
    """Stage 02 implements this from the authenticated request context."""
    def context_for(self, request_context) -> ResearchSecurityContext: ...


def stage01a_context() -> ResearchSecurityContext:
    """Pre-Stage-02 context: no user identity; external network allowed only through the controller's attested,
    policy-checked tool executions after operator approval of the research objective."""
    return ResearchSecurityContext(principal_id=None, tenant_id=None, network_permitted=True,
                                   approval_requirements=("operator_objective_approval",))
