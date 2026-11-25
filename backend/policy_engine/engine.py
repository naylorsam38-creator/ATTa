"""The central policy interface. Every protected API operation and every tool execution calls evaluate().

Stage 01A rules (Stage 02 adds authenticated users, tenants and approvals):
  * PUBLIC_OPERATIONS (liveness, readiness, version) are allowed.
  * `tool.execute` is the only other operation that can be allowed, and only when ALL hold:
      - the principal is the runtime controller (kind SERVICE) and its attestation, signed by this process's
        controller attestor, verifies — a Principal constructed elsewhere carries no authority;
      - the request is bound to a specific execution: contract hash, tool id, canonical arguments hash and
        execution id are present (correction 6 / §24.6) — the gateway re-derives them, the policy records them;
      - the tool's risk is LOW (medium and above need human approval, which does not exist until Stage 02);
      - every permission the tool requires is in the catalog, granted to the controller by trusted operator
        configuration, and present in the attested principal.
  * Every other operation is DENIED: no authentication exists until Stage 02.
  * Nobody can grant permissions: grant() always refuses (agents, capabilities and workers never, ever).
"""
from __future__ import annotations

import threading
from collections import deque
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable

from backend.contracts import Principal, PrincipalKind, RiskClass, get_logger, iso_utc

from .catalog import Catalog, load_catalog

log = get_logger("atta.policy")

PUBLIC_OPERATIONS = frozenset({"health.live", "health.ready", "health.controller", "system.version"})
BINDING_KEYS = ("contract_hash", "tool_id", "arguments_hash", "execution_id")
_NON_GRANTING_KINDS = frozenset({PrincipalKind.AGENT, PrincipalKind.CAPABILITY, PrincipalKind.WORKER,
                                 PrincipalKind.ANONYMOUS})


class Decision(str, Enum):
    ALLOW = "allow"
    DENY = "deny"


@dataclass(frozen=True)
class PolicyRequest:
    principal: Principal
    operation: str
    resource: str | None = None
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
    binding: dict[str, Any] = field(default_factory=dict)

    @property
    def allowed(self) -> bool:
        return self.decision is Decision.ALLOW


class SelfGrantError(PermissionError):
    pass


def _default_verifier(p: Principal) -> bool:
    from backend.authentication.attestation import get_attestor
    return get_attestor().verify(p)


class PolicyEngine:
    STAGE = "S01A"

    def __init__(self, catalog: Catalog | None = None, verifier: Callable[[Principal], bool] | None = None,
                 audit_size: int = 2000):
        if catalog is None:
            from backend.settings import REPO_ROOT
            catalog = load_catalog(REPO_ROOT / "configuration" / "permissions.yaml")
        self.catalog = catalog
        self._attestors: list = []
        self._verify = verifier or self._verify_registered
        self._audit: deque[PolicyDecision] = deque(maxlen=audit_size)
        self._lock = threading.Lock()

    def register_attestor(self, attestor) -> None:
        """In-process trusted components (the runtime controller) register the attestor that signs their principal."""
        with self._lock:
            if attestor not in self._attestors:
                self._attestors.append(attestor)

    def _verify_registered(self, p: Principal) -> bool:
        with self._lock:
            attestors = list(self._attestors)
        return any(a.verify(p) for a in attestors) or _default_verifier(p)

    def evaluate(self, req: PolicyRequest) -> PolicyDecision:
        if req.operation in PUBLIC_OPERATIONS and not req.required_permissions:
            return self._decide(Decision.ALLOW, "public operation", req)
        if req.operation != "tool.execute":
            return self._decide(Decision.DENY, "protected operation denied by default: authentication and "
                                "authorisation are not available until Stage 02", req)
        p = req.principal
        if p.kind is not PrincipalKind.SERVICE or not p.id.startswith("controller:") or not self._verify(p):
            return self._decide(Decision.DENY, "tool execution is only accepted from the attested runtime "
                                "controller (execution gateway)", req)
        missing_binding = [k for k in BINDING_KEYS if not req.context.get(k)]
        if missing_binding:
            return self._decide(Decision.DENY, f"request not bound to an execution: missing {missing_binding}", req)
        if req.risk is None or req.risk.value in self.catalog.approval_required_risks:
            return self._decide(Decision.DENY, f"risk '{req.risk.value if req.risk else 'unknown'}' needs human "
                                "approval, which is not available until Stage 02", req)
        unknown = sorted(x for x in req.required_permissions if x not in self.catalog.permissions)
        if unknown:
            return self._decide(Decision.DENY, f"permissions not in the catalog: {unknown}", req)
        not_granted = sorted(set(req.required_permissions) - (self.catalog.controller_granted & p.permissions))
        if not_granted:
            return self._decide(Decision.DENY, f"permissions not granted to the controller: {not_granted}", req)
        return self._decide(Decision.ALLOW, "low-risk tool within the controller's granted permissions", req)

    def grant(self, grantor: Principal, grantee: Principal, permission: str) -> None:
        if grantor.kind in _NON_GRANTING_KINDS:
            reason = f"{grantor.kind.value} principals may never grant permissions"
        elif grantor.id == grantee.id:
            reason = "principals may not grant permissions to themselves"
        else:
            reason = "permissions come only from trusted operator configuration (Stage 02 adds roles)"
        self._decide(Decision.DENY, f"grant '{permission}' refused: {reason}",
                     PolicyRequest(principal=grantor, operation="policy.grant", resource=grantee.id))
        raise SelfGrantError(reason)

    def audit_log(self) -> list[PolicyDecision]:
        with self._lock:
            return list(self._audit)

    def _decide(self, decision: Decision, reason: str, req: PolicyRequest) -> PolicyDecision:
        d = PolicyDecision(decision=decision, reason=reason, operation=req.operation, resource=req.resource,
                           principal_id=req.principal.id, decided_at=iso_utc(),
                           binding={k: req.context.get(k) for k in BINDING_KEYS if req.context.get(k)})
        with self._lock:
            self._audit.append(d)
        log.info("policy decision", extra={"decision": decision.value, "operation": req.operation,
                                           "resource": req.resource, "principal": req.principal.id,
                                           "reason": reason})
        return d


_engine: PolicyEngine | None = None
_engine_lock = threading.Lock()


def get_policy_engine() -> PolicyEngine:
    global _engine
    with _engine_lock:
        if _engine is None:
            _engine = PolicyEngine()
        return _engine
