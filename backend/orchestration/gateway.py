"""The controlled execution gateway (Stage 01A §15) — the ONLY route by which a proposed tool action executes.

Sequence (each step can only refuse, never skip):
  1 authenticate the worker context      2 load the authoritative contract   3 verify version + integrity
  4 validate task state + attempt        5 tool identifier known             6 tool in the assigned pool
  7 operation + input arguments          8 resource / path / network scope   9 capability + dependency health
 10 drift checks                         11 budgets + deadline               12 central policy engine
 13 approvals                            14 reserve (execution, budgets, locks)
 15 execute in the supervised runtime    16 validate output                  17 record result + evidence
 18 update persistent task state
"""
from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from datetime import timezone
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

from backend.authentication.attestation import Attestor
from backend.capability_registry import CapabilityNotExecutable, CapabilityRegistry
from backend.capability_runtime import CapabilityRuntime, ExecutionTicket
from backend.contracts import PrincipalKind, RiskClass, canonical_hash, get_logger, new_id
from backend.policy_engine import PolicyEngine, PolicyRequest

from .catalogue import ToolCatalogue
from .config import ControllerConfig
from .contracts import ToolProposal
from .drift import READ_ONLY_OPERATIONS, DriftGuard, Violation
from .store import ControlStore

log = get_logger("atta.gateway")


@dataclass(frozen=True)
class WorkerContext:
    """Issued by the controller when an attempt starts. Its signature binds worker, task, attempt and contract."""
    objective_id: str
    task_id: str
    attempt_id: str
    contract_hash: str
    worker_id: str
    expires_at: float
    signature: str


@dataclass
class GatewayResult:
    status: str                                   # executed | rejected | failed | replayed
    execution_id: str | None = None
    output: Any = None
    evidence_id: str | None = None
    violations: list[Violation] = field(default_factory=list)
    reason: str = ""
    policy: dict = field(default_factory=dict)
    task_state: str | None = None


def dir_size_mb(path: Path) -> float:
    total = 0
    for dp, _, fns in os.walk(path):
        for f in fns:
            try:
                total += os.lstat(os.path.join(dp, f)).st_size
            except OSError:
                pass
    return total / (1024 * 1024)


class ExecutionGateway:
    def __init__(self, *, store: ControlStore, registry: CapabilityRegistry, catalogue: ToolCatalogue,
                 policy: PolicyEngine, runtime: CapabilityRuntime, config: ControllerConfig, attestor: Attestor,
                 controller_id: str, workspace_root: Path):
        self.store, self.registry, self.catalogue, self.policy = store, registry, catalogue, policy
        self.runtime, self.config, self.attestor = runtime, config, attestor
        self.controller_id, self.workspace_root = controller_id, workspace_root
        self.drift = DriftGuard(config.limits.no_progress_window, config.protected_path_prefixes)
        self._gateway_key = Attestor()
        runtime.bind_gateway(self._gateway_key)
        policy.register_attestor(attestor)

    # ---------------------------------------------------------------- helpers
    def issue_context(self, objective_id: str, task_id: str, attempt_id: str, contract_hash: str, worker_id: str,
                      ttl_s: float = 3600) -> WorkerContext:
        exp = time.time() + ttl_s
        sig = self.attestor.sign_fields("worker", objective_id, task_id, attempt_id, contract_hash, worker_id, exp)
        return WorkerContext(objective_id, task_id, attempt_id, contract_hash, worker_id, exp, sig)

    def _verify_context(self, ctx: WorkerContext) -> bool:
        return ctx.expires_at > time.time() and self.attestor.verify_fields(
            ctx.signature, "worker", ctx.objective_id, ctx.task_id, ctx.attempt_id, ctx.contract_hash, ctx.worker_id,
            ctx.expires_at)

    def controller_principal(self):
        return self.attestor.issue(id=f"controller:{self.controller_id}", kind=PrincipalKind.SERVICE,
                                   permissions=self.policy.catalog.controller_granted, authenticated=True)

    def workspace(self, objective_id: str) -> Path:
        p = self.workspace_root / objective_id
        p.mkdir(parents=True, exist_ok=True)
        return p

    def _reject(self, ctx: WorkerContext, proposal_id: str | None, violations: list[Violation], reason: str,
                **kw) -> GatewayResult:
        for v in violations:
            self.store.record_drift(ctx.objective_id, ctx.task_id, proposal_id, v.rule, f"{v.field}: {v.detail}")
        log.warning("gateway rejected %s: %s", proposal_id, reason, extra={"task": ctx.task_id,
                                                                           "rules": [v.rule for v in violations]})
        return GatewayResult("rejected", violations=violations, reason=reason, **kw)

    def _task_started(self, objective_id: str, task_id: str, attempt_id: str) -> float | None:
        for t in self.store.transitions(objective_id, task_id):
            if t["to_state"] == "RUNNING" and t["attempt_id"] == attempt_id:
                return t["at"].astimezone(timezone.utc).timestamp()
        return None

    # ---------------------------------------------------------------- the gateway
    def execute(self, ctx: WorkerContext, proposal: ToolProposal) -> GatewayResult:
        # 1. authenticate the execution context
        if not self._verify_context(ctx):
            return self._reject(ctx, proposal.proposal_id, [Violation("unauthenticated_context", "context",
                                "worker context signature invalid or expired")], "unauthenticated execution context")
        # 2-3. authoritative contract, version and integrity
        try:
            c = self.store.contract(ctx.objective_id, ctx.task_id)
        except Exception as exc:  # noqa: BLE001
            return self._reject(ctx, proposal.proposal_id, [Violation("contract_integrity", "contract", str(exc))],
                                "contract unavailable or corrupted")
        if c.contract_hash != ctx.contract_hash:
            return self._reject(ctx, proposal.proposal_id, [Violation("contract_version_mismatch", "context",
                                "the worker's contract is not the current authoritative version")], "stale contract")
        # 4. task state + attempt
        task = self.store.task(ctx.objective_id, ctx.task_id)
        if task["state"] != "RUNNING" or task["attempt_id"] != ctx.attempt_id:
            return self._reject(ctx, proposal.proposal_id, [Violation("invalid_state", "task",
                                f"task is {task['state']} (attempt {task['attempt_id']})")], "task not running")
        # 5-8, 10. identifiers, pool, operation, arguments, scope, drift
        tool = self.catalogue.get(proposal.tool_id)
        step_key = canonical_hash({"tool": proposal.tool_id, "operation": proposal.operation,
                                   "arguments": proposal.arguments})
        history = [e for e in self.store.executions(ctx.objective_id, ctx.task_id) if e["attempt_id"] == ctx.attempt_id]
        violations, resolved = self.drift.check_proposal(
            proposal, c, tool, self.workspace(ctx.objective_id), history,
            self.store.step_failures(ctx.objective_id, ctx.task_id, step_key), c.maximum_retries)
        if violations:
            return self._reject(ctx, proposal.proposal_id, violations, "drift guard: " + violations[0].rule)
        # 9. capability + dependency health, revalidated now
        try:
            self.registry.revalidate(tool.spec.capability_id)
        except CapabilityNotExecutable as exc:
            return self._reject(ctx, proposal.proposal_id, [Violation("capability_unavailable", "tool_id", exc.message)],
                                "capability not executable")
        # 11. budgets + deadline
        started = self._task_started(ctx.objective_id, ctx.task_id, ctx.attempt_id) or time.time()
        remaining_s = min(c.execution_deadline, self.config.limits.max_task_duration_s) - (time.time() - started)
        if remaining_s <= 0:
            self.store.transition(ctx.objective_id, ctx.task_id, "FAILED", "execution deadline exceeded",
                                  self.controller_id, expect=["RUNNING"])
            return self._reject(ctx, proposal.proposal_id, [Violation("deadline_exceeded", "task",
                                "the task's execution deadline has passed")], "deadline exceeded", task_state="FAILED")
        budgets = self.store.budgets(ctx.objective_id, ctx.task_id)
        if budgets.get("actions", {}).get("remaining", 0) < 1:
            self.store.transition(ctx.objective_id, ctx.task_id, "BLOCKED", "maximum actions reached",
                                  self.controller_id, expect=["RUNNING"])
            return self._reject(ctx, proposal.proposal_id, [Violation("action_budget_exhausted", "task",
                                f"maximum of {c.maximum_actions} actions reached")], "action budget exhausted",
                                task_state="BLOCKED")
        # 12. central policy engine, bound to this exact execution
        execution_id = new_id("exe")
        args_hash = canonical_hash(proposal.arguments)
        decision = self.policy.evaluate(PolicyRequest(
            principal=self.controller_principal(), operation="tool.execute", resource=proposal.tool_id,
            risk=RiskClass(tool.spec.risk.value), required_permissions=tool.spec.permissions,
            context={"contract_hash": c.contract_hash, "tool_id": proposal.tool_id, "arguments_hash": args_hash,
                     "execution_id": execution_id}))
        policy = {"decision": decision.decision.value, "reason": decision.reason, "binding": decision.binding}
        if not decision.allowed:
            return self._reject(ctx, proposal.proposal_id, [Violation("policy_denied", "tool_id", decision.reason)],
                                "policy denied", policy=policy)
        # 13. approvals (none can be granted before Stage 02)
        if c.approval_requirements:
            return self._reject(ctx, proposal.proposal_id, [Violation("approval_required", "contract",
                                f"approvals {c.approval_requirements} are required and approvals arrive in Stage 02")],
                                "approval required", policy=policy)
        # 14. reserve: idempotent execution row, budgets, resource locks
        row, fresh = self.store.reserve_execution({
            "execution_id": execution_id, "objective_id": ctx.objective_id, "task_id": ctx.task_id,
            "attempt_id": ctx.attempt_id, "proposal_id": proposal.proposal_id, "tool_number": proposal.tool_id,
            "tool_string": tool.spec.string_id, "operation": proposal.operation, "request_hash": step_key,
            "arguments": proposal.arguments, "idempotency": tool.spec.idempotency, "policy": policy,
            "lease_s": max(1.0, min(tool.spec.timeout_s, remaining_s)) + 30})
        if not fresh:
            if row["request_hash"] != step_key:
                return self._reject(ctx, proposal.proposal_id, [Violation("idempotency_conflict", "proposal_id",
                                    "proposal id reused for a different action")], "duplicate proposal id")
            if row["state"] in ("reserved", "running"):
                return self._reject(ctx, proposal.proposal_id, [Violation("execution_in_progress", "proposal_id",
                                    "this proposal is already executing")], "duplicate concurrent execution")
            return GatewayResult("replayed", execution_id=row["execution_id"], output=row["result"],
                                 reason=f"already {row['state']}; result replayed, not re-executed")
        if not self.store.reserve(ctx.objective_id, ctx.task_id, "concurrent_tools", 1):
            self.store.finish_execution(execution_id, "failed", error={"reason": "concurrency limit"})
            return self._reject(ctx, proposal.proposal_id, [Violation("concurrency_limit", "task",
                                f"maximum of {c.maximum_parallel_tools} concurrent tool executions")],
                                "concurrent tool limit reached")
        if not self.store.reserve(ctx.objective_id, ctx.task_id, "actions", 1):
            self.store.settle(ctx.objective_id, ctx.task_id, "concurrent_tools", 1, 0)
            self.store.finish_execution(execution_id, "failed", error={"reason": "action budget"})
            return self._reject(ctx, proposal.proposal_id, [Violation("action_budget_exhausted", "task",
                                "no action budget left")], "action budget exhausted")
        writes = proposal.operation not in READ_ONLY_OPERATIONS
        if writes and proposal.target_resources:
            family = {ctx.task_id} | ({task["repair_of"]} if task.get("repair_of") else set())
            conflicts = self.store.lock_resources(ctx.objective_id, ctx.task_id, proposal.target_resources, family)
            if conflicts:
                self.store.settle(ctx.objective_id, ctx.task_id, "concurrent_tools", 1, 0)
                self.store.settle(ctx.objective_id, ctx.task_id, "actions", 1, 0)
                self.store.finish_execution(execution_id, "failed", error={"reason": "resource locked"})
                return self._reject(ctx, proposal.proposal_id, [Violation("resource_conflict", "target_resources",
                                    f"locked by another task: {conflicts}")], "conflicting resource modification")
        # 15. execute the registered handler in the supervised runtime
        if not self.store.claim_execution(execution_id):
            self.store.settle(ctx.objective_id, ctx.task_id, "concurrent_tools", 1, 0)
            self.store.settle(ctx.objective_id, ctx.task_id, "actions", 1, 0)
            return self._reject(ctx, proposal.proposal_id, [Violation("execution_in_progress", "proposal_id",
                                "execution already claimed")], "duplicate execution")
        reg = self.registry.get(tool.spec.capability_id)
        exp = time.time() + 60
        ticket = ExecutionTicket(execution_id, tool.spec.capability_id, "execute", exp,
                                 self._gateway_key.sign_fields(execution_id, tool.spec.capability_id, "execute", exp))
        handlers = dict(reg.manifest.handler_refs)
        handlers["execute"] = tool.spec.handler
        workspace = self.workspace(ctx.objective_id)
        timeout = max(0.5, min(tool.spec.timeout_s, remaining_s))
        mem = int(tool.spec.resource_limits.get("memory_mb") or c.resource_budget.memory_mb)
        self.runtime.memory_limit_mb = min(mem, c.resource_budget.memory_mb)
        t0 = time.monotonic()
        run = self.runtime.run(ticket=ticket, mode="execute", capability_id=tool.spec.capability_id,
                               package_dir=reg.manifest.directory, expected_digest=reg.package_digest,
                               handlers=handlers, timeout_s=timeout, payload=resolved, workdir=workspace,
                               context={"execution_id": execution_id, "task_id": ctx.task_id,
                                        "objective_id": ctx.objective_id, "operation": proposal.operation,
                                        "workspace": str(workspace)})
        elapsed = time.monotonic() - t0
        self.store.settle(ctx.objective_id, ctx.task_id, "concurrent_tools", 1, 0)
        self.store.settle(ctx.objective_id, ctx.task_id, "actions", 1, 1)
        self.store.consume(ctx.objective_id, ctx.task_id, "cpu_seconds", round(elapsed, 3))
        # 16. validate the actual output
        state, error, output = "succeeded", None, run.value
        if run.status != "ok":
            state = {"timeout": "timed_out", "cancelled": "cancelled"}.get(run.status, "failed")
            if state in ("timed_out", "cancelled") and tool.spec.idempotency == "external_uncertain":
                state = "outcome_unknown"   # killing the local process does not undo an external action
            error = {"status": run.status, "error_type": run.error_type, "error": run.error,
                     "leftovers_killed": run.leftovers_killed, "survivors": run.survivors}
            output = None
        else:
            errs = [e.message for e in Draft202012Validator(tool.spec.output_schema).iter_errors(output)] \
                if isinstance(output, dict) else ["output is not an object"]
            if errs:
                state, error, output = "failed", {"status": "invalid_output", "errors": errs[:5]}, None
            elif dir_size_mb(workspace) > c.resource_budget.disk_mb:
                state, error = "failed", {"status": "disk_budget_exceeded", "limit_mb": c.resource_budget.disk_mb}
        # 17. record result and evidence
        out_hash = canonical_hash(output) if output is not None else None
        evidence = {"execution_id": execution_id, "proposal_id": proposal.proposal_id, "tool_id": proposal.tool_id,
                    "tool_string": tool.spec.string_id, "operation": proposal.operation, "arguments_hash": args_hash,
                    "state": state, "output": output, "output_hash": out_hash, "error": error, "policy": policy,
                    "duration_s": run.duration_s, "leftovers_killed": run.leftovers_killed,
                    "referenced_requirement_ids": proposal.referenced_requirement_ids}
        eid = self.store.add_evidence(ctx.objective_id, ctx.task_id, "tool_execution", evidence)
        self.store.finish_execution(execution_id, state, result=output, error=error, output_hash=out_hash)
        # 18. persistent task state
        task_state = "RUNNING"
        if state != "succeeded":
            self.store.step_failure(ctx.objective_id, ctx.task_id, step_key)
            if state == "outcome_unknown":
                self.store.transition(ctx.objective_id, ctx.task_id, "BLOCKED", "external outcome uncertain: "
                                      "reconciliation required before any retry", self.controller_id,
                                      expect=["RUNNING"], evidence={"execution_id": execution_id})
                task_state = "BLOCKED"
            elif not self.store.consume(ctx.objective_id, ctx.task_id, "failed_steps", 1) or \
                    self.store.budgets(ctx.objective_id, ctx.task_id)["failed_steps"]["remaining"] <= 0:
                self.store.transition(ctx.objective_id, ctx.task_id, "FAILED", "maximum failed steps exceeded",
                                      self.controller_id, expect=["RUNNING"], evidence={"execution_id": execution_id})
                task_state = "FAILED"
            return GatewayResult("failed", execution_id=execution_id, evidence_id=eid, policy=policy,
                                 reason=f"tool execution {state}", output=error, task_state=task_state)
        return GatewayResult("executed", execution_id=execution_id, output=output, evidence_id=eid, policy=policy,
                             task_state=task_state)

    def cancel(self, execution_id: str) -> bool:
        return self.runtime.cancel(execution_id)
