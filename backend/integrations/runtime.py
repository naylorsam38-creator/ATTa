"""Stage 12 enforcement points wired into the EXISTING Stage 01A machinery (no second engine):

  IntegrationTrustGate   — the registry's trust store for `integration.*` capabilities. The registry calls it on
                           every evaluation, including revalidate() immediately before each execution, so disable,
                           trust, offline, isolation and credential state take effect at once. All other capabilities
                           are delegated unchanged to the Stage 01A operator-signed TrustStore.
  ExecutionGuard         — gateway guard (step 9b): ENABLED state, version pin, network policy, credentials, audit.
  ResultFilter           — gateway result filter (step 16b): bounded size, secret redaction, injection flags,
                           provenance + audit. Output stays DATA; it reaches a model only as `untrusted` content.
  AuditingAllocator      — wraps the deterministic allocator to audit integration tool allocations (no decisions).
  RejectionAuditor       — records gateway refusals that concern integration tools (permission denials, approvals)."""
from __future__ import annotations

import json
from typing import TYPE_CHECKING

from backend.capability_registry.trust import TrustStore
from backend.contracts import canonical_hash, canonical_json
from backend.orchestration.drift import Violation

from .assessment import assess
from .content import injection_findings, redact, secret_findings, walk_strings
from .types import CAPABILITY_ID_PREFIX, IntegrationState, RISK_ORDER

if TYPE_CHECKING:
    from .manager import IntegrationManager


class IntegrationTrustGate(TrustStore):
    def __init__(self, base: TrustStore, manager: "IntegrationManager"):
        super().__init__(base.entries, base.problems, base.source)
        self.base, self.manager = base, manager

    def check(self, manifest, digest: str):
        if not manifest.id.startswith(CAPABILITY_ID_PREFIX):
            return self.base.check(manifest, digest)
        rec = self.manager.by_capability(manifest.id)
        if rec is None:
            return "untrusted", [f"'{manifest.id}' has no approved Stage 12 installation record"]
        if rec.package_digest != digest or rec.version != manifest.version:
            return "integrity_failed", ["installed package differs from the approved, verified package"]
        extra = sorted(set(manifest.permissions) - set(rec.manifest.get("permissions_requested") or []))
        if extra:
            return "untrusted", [f"permissions not approved by the operator: {extra}"]
        if manifest.risk.value != rec.max_risk:
            return "untrusted", [f"risk {manifest.risk.value} differs from the approved risk {rec.max_risk}"]
        a = assess(rec, network=self.manager.network, isolation=self.manager.isolation, broker=self.manager.broker,
                   lookup=self.manager.get_installed,
                   allow_states=frozenset({IntegrationState.ENABLED, IntegrationState.VERIFYING}))
        if a.blocked:
            return "untrusted", a.blocked
        if a.stopped:
            return "disabled", a.stopped
        if a.offline or a.auth or a.dependencies:
            return "dependency_missing", a.offline + a.auth + a.dependencies
        return None, []


class ExecutionGuard:
    def __init__(self, manager: "IntegrationManager"):
        self.manager = manager

    def __call__(self, *, ctx, contract, tool, proposal):
        rec = self.manager.by_capability(tool.spec.capability_id)
        if rec is None:
            return None if not tool.spec.capability_id.startswith(CAPABILITY_ID_PREFIX) else \
                Violation("integration_unknown", "tool_id", "no installation record for this integration tool")
        store = self.manager.store
        a = assess(rec, network=self.manager.network, isolation=self.manager.isolation, broker=self.manager.broker,
                   lookup=self.manager.get_installed)
        base = {"objective_id": ctx.objective_id, "task_id": ctx.task_id, "tool_id": proposal.tool_id,
                "tool_string": tool.spec.string_id, "proposal_id": proposal.proposal_id}
        if not a.executable:
            rule = "integration_offline" if a.offline and not a.blocked and not a.stopped else \
                "integration_auth_required" if a.auth and not a.blocked and not a.stopped else "integration_not_executable"
            store.event(rec.integration_id, "execution_denied", "execution-guard", {**base, "reasons": a.reasons()},
                        version=rec.version)
            return Violation(rule, "tool_id", "; ".join(a.reasons())[:500])
        pin = store.pin(ctx.objective_id, ctx.task_id, proposal.tool_id, rec.integration_id, rec.version,
                        rec.package_digest or "")
        if pin["version"] != rec.version or pin["package_digest"] != (rec.package_digest or ""):
            store.event(rec.integration_id, "execution_denied", "execution-guard",
                        {**base, "reason": "version pin mismatch", "pinned_version": pin["version"],
                         "installed_version": rec.version}, version=rec.version)
            return Violation("integration_version_mismatch", "tool_id",
                             f"task is pinned to {rec.integration_id} {pin['version']}; installed is {rec.version}. "
                             "An upgraded integration is never swapped into an active task silently.")
        store.event(rec.integration_id, "execution_authorized_by_guard", "execution-guard",
                    {**base, "pinned_version": pin["version"], "package_digest": pin["package_digest"]},
                    version=rec.version)
        return None


class ResultFilter:
    def __init__(self, manager: "IntegrationManager"):
        self.manager = manager

    def __call__(self, *, tool, proposal, output, execution_id, ctx):
        rec = self.manager.by_capability(tool.spec.capability_id)
        if rec is None:
            return output, None
        t = next((x for x in rec.tools if x["name"] == tool.spec.name), {})
        limit = min(int(t.get("max_result_bytes") or self.manager.config.max_result_bytes),
                    self.manager.config.max_result_bytes)
        size = len(canonical_json(output).encode("utf-8"))
        prov = {"execution_id": execution_id, "objective_id": ctx.objective_id, "task_id": ctx.task_id,
                "tool_id": proposal.tool_id, "tool_string": tool.spec.string_id, "integration_id": rec.integration_id,
                "integration_version": rec.version, "package_digest": rec.package_digest, "bytes": size,
                "trust": "untrusted_tool_output"}
        if size > limit:
            self.manager.store.event(rec.integration_id, "result_rejected", "result-filter",
                                     {**prov, "reason": "result_too_large", "limit": limit}, version=rec.version)
            return None, {"status": "result_too_large", "bytes": size, "limit": limit,
                          "integration_id": rec.integration_id}
        leaked = secret_findings(output, in_schema=True)   # value patterns only (keys of free-form data are data)
        if leaked:
            output = redact(output)
        flags = sorted({f for _, s in walk_strings(output) for f in injection_findings(s)})
        self.manager.store.event(rec.integration_id, "tool_executed", "result-filter",
                                 {**prov, "output_hash": canonical_hash(output), "injection_flags": flags,
                                  "secrets_redacted": len(leaked)}, version=rec.version)
        return output, None


class AuditingAllocator:
    """Delegates every decision to the Stage 01A ToolAllocator; records integration tool allocations."""

    def __init__(self, inner, manager: "IntegrationManager"):
        self.inner, self.manager = inner, manager

    def __getattr__(self, name):
        return getattr(self.inner, name)

    def allocate(self, req, proposed=None):
        res = self.inner.allocate(req, proposed)
        for number in (res.tool_ids if res.status == "ALLOCATED" else []):
            rec = self.manager.by_number(number)
            if rec is not None:
                self.manager.store.event(rec.integration_id, "tool_allocated", "allocator",
                                         {"task_type": req.task_type, "tool_id": number, "pool": res.tool_ids},
                                         version=rec.version)
        if proposed:
            for number in proposed:
                rec = self.manager.by_number(number)
                if rec is not None and number not in res.tool_ids:
                    self.manager.store.event(rec.integration_id, "allocation_denied", "allocator",
                                             {"task_type": req.task_type, "tool_id": number, "status": res.status,
                                              "reason": res.rejected.get(number) or "; ".join(res.missing)[:300]},
                                             version=rec.version)
        return res


class RejectionAuditor:
    """Wraps ExecutionGateway.execute (composition, no change to its logic): when the gateway refuses a proposal for
    an integration tool, the refusal (permission denial, approval requirement, unassigned tool ...) is audited."""

    def __init__(self, manager: "IntegrationManager", execute):
        self.manager, self.execute = manager, execute

    def __call__(self, ctx, proposal):
        res = self.execute(ctx, proposal)
        try:
            if res.status == "rejected":
                rec = self.manager.by_number(proposal.tool_id)
                if rec is not None:
                    for v in res.violations:
                        if v.rule.startswith("integration_"):
                            continue      # recorded by the guard
                        etype = {"approval_required": "approval_required", "policy_denied": "permission_denied",
                                 "tool_not_in_pool": "unassigned_tool_denied"}.get(v.rule, "execution_refused")
                        if v.rule == "policy_denied" and "approval" in v.detail:
                            etype = "approval_required"
                        self.manager.store.event(rec.integration_id, etype, "gateway",
                                                 {"objective_id": ctx.objective_id, "task_id": ctx.task_id,
                                                  "tool_id": proposal.tool_id, "rule": v.rule,
                                                  "detail": v.detail[:300], "reason": res.reason,
                                                  "policy": res.policy}, version=rec.version)
        except Exception:  # noqa: BLE001 — auditing never changes the gateway's decision
            pass
        return res


def risk_at_least(a: str, b: str) -> bool:
    return RISK_ORDER[a] >= RISK_ORDER[b]


def _unused():   # keep json imported for type-checkers of generated content
    return json
