"""The central policy interface. Every capability execution and every protected API operation calls evaluate().

Stage 01 rules (until Stage 02 delivers authentication and authorisation):
  * Operations explicitly listed in PUBLIC_OPERATIONS are allowed (liveness, readiness, version).
  * Everything else is DENIED — there is no authorisation backend yet, so no principal can hold a permission
    that the engine trusts.
  * No agent, capability or worker may grant permissions — to itself or anyone. grant() refuses them always,
    and at Stage 01 refuses every grantor because no authorisation authority exists.
Every decision is recorded in an in-memory audit ring (and logged) with its reason.
"""
from __future__ import annotations

import threading
from collections import deque
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from backend.contracts import Principal, PrincipalKind, RiskClass, get_logger, iso_utc

log = get_logger("atta.policy")

PUBLIC_OPERATIONS = frozenset({"health.live", "health.ready", "system.version"})
_NON_GRANTING_KINDS = frozenset({PrincipalKind.AGENT, PrincipalKind.CAPABILITY, PrincipalKind.WORKER,
                                 PrincipalKind.ANONYMOUS})


class Decision(str, Enum):
    ALLOW = "allow"
    DENY = "deny"


@dataclass(frozen=True)
class PolicyRequest:
    principal: Principal
    operation: str                      # e.g. "capability.execute", "health.ready"
    resource: str | None = None         # e.g. a capability id
    risk: RiskClass | None = None
    required_permissions: frozenset[str] = frozenset()
    context: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class PolicyDecision:
    decision: Decision
    reason: str
    operation: str
    resource: str | None
    principal_id: str
    decided_at: str

    @property
    def allowed(self) -> bool:
        return self.decision is Decision.ALLOW


class SelfGrantError(PermissionError):
    pass


class PolicyEngine:
    STAGE = "S01"

    def __init__(self, audit_size: int = 1000):
        self._audit: deque[PolicyDecision] = deque(maxlen=audit_size)
        self._lock = threading.Lock()

    def evaluate(self, req: PolicyRequest) -> PolicyDecision:
        if req.operation in PUBLIC_OPERATIONS and not req.required_permissions:
            d = self._decide(Decision.ALLOW, "public operation", req)
        else:
            d = self._decide(Decision.DENY, "protected operation denied by default: authentication and "
                             "authorisation are not available until Stage 02", req)
        return d

    def grant(self, grantor: Principal, grantee: Principal, permission: str) -> None:
        """Permission grants. Stage 01 has no authorisation authority, so every grant is refused."""
        if grantor.kind in _NON_GRANTING_KINDS:
            reason = f"{grantor.kind.value} principals may never grant permissions"
        elif grantor.id == grantee.id:
            reason = "principals may not grant permissions to themselves"
        else:
            reason = "no authorisation authority exists before Stage 02"
        self._decide(Decision.DENY, f"grant '{permission}' refused: {reason}",
                     PolicyRequest(principal=grantor, operation="policy.grant", resource=grantee.id))
        raise SelfGrantError(reason)

    def audit_log(self) -> list[PolicyDecision]:
        with self._lock:
            return list(self._audit)

    def _decide(self, decision: Decision, reason: str, req: PolicyRequest) -> PolicyDecision:
        d = PolicyDecision(decision=decision, reason=reason, operation=req.operation, resource=req.resource,
                           principal_id=req.principal.id, decided_at=iso_utc())
        with self._lock:
            self._audit.append(d)
        log.info("policy decision", extra={"decision": decision.value, "operation": req.operation,
                                           "resource": req.resource, "principal": req.principal.id,
                                           "reason": reason})
        return d


_engine: PolicyEngine | None = None
_engine_lock = threading.Lock()


def get_policy_engine() -> PolicyEngine:
    """The single process-wide policy engine."""
    global _engine
    with _engine_lock:
        if _engine is None:
            _engine = PolicyEngine()
        return _engine
