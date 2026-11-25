"""The runtime intelligence controller (Stage 01A) — the single intelligence engine's control loop.

USER OBJECTIVE -> OUTCOME CONTRACT -> REQUIREMENTS -> TASK DECOMPOSITION -> DEPENDENCY VALIDATION -> TASK CONTRACTS ->
NUMBERED TOOL ALLOCATION -> CONTEXT ASSEMBLY -> MODEL PROPOSAL -> STRUCTURAL/DRIFT/PERMISSION/APPROVAL VALIDATION
(gateway) -> CONTROLLED EXECUTION -> RESULT VALIDATION -> INDEPENDENT VERIFICATION -> CHECKPOINT -> NEXT AUTHORISED
TASK -> INTEGRATION -> FINAL VERIFICATION -> DELIVERY.

Every transition is a method here backed by the persistent state machine; a model only ever supplies proposals
(data), which this code parses, validates and routes through the ExecutionGateway."""
from __future__ import annotations

import hashlib
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from backend.authentication.attestation import Attestor
from backend.capability_registry import CapabilityRegistry
from backend.capability_runtime import CapabilityRuntime
from backend.contracts import canonical_hash, get_logger, iso_utc, new_id
from backend.policy_engine import PolicyEngine

from .allocation import ToolAllocator, ToolRequirement
from .authorization import AuthorizationVerifier, OperatorAuthorization
from .catalogue import ToolCatalogue
from .config import ControllerConfig
from .context import ContextAssembler, ContextTooLarge
from .contracts import (CompletionClaim, OutcomeContract, OutputSpec, ResourceBudget, TaskContract, TaskCriterion,
                        ToolProposal)
from .drift import DriftGuard
from .gateway import ExecutionGateway
from .models import ModelAdapter
from .proposals import parse_proposal
from .states import ACTIVE_STATES
from .store import ControlStore
from .traceability import trace
from .verification import IndependentVerifier

log = get_logger("atta.controller")
IMMUTABLE_OBJECTIVE_FIELDS = frozenset({"objective_id", "original_instructions", "original_blueprint",
                                        "original_source_sha256", "contract_version", "created_at", "approval_status"})


class ControllerError(Exception):
    def __init__(self, message: str, details: Any = None):
        super().__init__(message)
        self.details = details


class PlanningBlocked(ControllerError):
    pass


@dataclass
class TaskSpec:
    """What decomposition (a model's proposal or an operator) asks for. The controller turns it into a contract."""
    task_id: str
    requirement_ids: list[str]
    task_type: str
    description: str
    required_outcome: str
    operations: list[str]
    acceptance_criteria: list[dict]
    dependency_task_ids: list[str] = field(default_factory=list)
    prohibited_operations: list[str] = field(default_factory=list)
    required_outputs: list[dict] = field(default_factory=list)
    output_schemas: dict = field(default_factory=dict)
    permitted_inputs: list[str] = field(default_factory=list)
    input_checksums: dict = field(default_factory=dict)
    permitted_resource_ids: list[str] = field(default_factory=list)
    permitted_filesystem_paths: list[str] = field(default_factory=lambda: ["."])
    permitted_network_destinations: list[str] = field(default_factory=list)
    proposed_tool_ids: list[str] | None = None
    input_fields: list[str] = field(default_factory=list)
    resource_types: list[str] = field(default_factory=list)
    approval_requirements: list[str] = field(default_factory=list)
    rollback_procedure: str = "discard the task workspace outputs; restore the last verified checkpoint"
    assigned_worker: str = "worker-1"
    maximum_actions: int | None = None
    maximum_retries: int | None = None
    maximum_failed_steps: int | None = None
    maximum_parallel_tools: int | None = None
    execution_deadline: float | None = None
    resource_budget: dict | None = None
    parent_task_id: str | None = None
    max_risk: str = "low"


class RuntimeController:
    def __init__(self, *, store: ControlStore, registry: CapabilityRegistry, catalogue: ToolCatalogue,
                 policy: PolicyEngine, runtime: CapabilityRuntime, config: ControllerConfig, workspace_root: Path,
                 authorizer: AuthorizationVerifier, controller_id: str | None = None,
                 attestor: Attestor | None = None, gateway: ExecutionGateway | None = None):
        self.store, self.registry, self.catalogue, self.policy = store, registry, catalogue, policy
        self.runtime, self.config, self.authorizer = runtime, config, authorizer
        self.controller_id = controller_id or new_id("svc")
        self.attestor = attestor or Attestor()
        self.workspace_root = workspace_root
        self.gateway = gateway or ExecutionGateway(store=store, registry=registry, catalogue=catalogue, policy=policy,
                                                   runtime=runtime, config=config, attestor=self.attestor,
                                                   controller_id=self.controller_id, workspace_root=workspace_root)
        self.allocator = ToolAllocator(catalogue, policy.catalog, config)
        self.assembler = ContextAssembler(catalogue)
        self.verifier = IndependentVerifier(store, registry, runtime, catalogue, config.protected_path_prefixes)

    # ================================================================== objectives (§4)
    def create_objective(self, instructions: str, *, blueprint: str | None = None, **fields) -> OutcomeContract:
        oid = new_id("obj")
        source = (instructions + "\0" + (blueprint or "")).encode("utf-8")
        c = OutcomeContract.model_validate({
            "objective_id": oid, "original_instructions": instructions, "original_blueprint": blueprint,
            "original_source_sha256": hashlib.sha256(source).hexdigest(), "contract_version": 1,
            "created_at": iso_utc(), "approval_status": "draft", **fields})
        self.store.insert_objective(c, max_active=self.config.limits.max_active_tasks_per_objective)
        return c

    def approve_objective(self, objective_id: str, auth: OperatorAuthorization) -> OutcomeContract:
        cur, _ = self.store.objective(objective_id)
        if cur.approval_status == "approved":
            return cur
        operator = self.authorizer.verify(auth, "approve_objective", objective_id, cur.hash())
        new = cur.model_copy(update={"contract_version": cur.contract_version + 1, "approval_status": "approved"})
        self.store.insert_objective(new, approved_by=operator, max_active=self.config.limits.max_active_tasks_per_objective)
        return new

    def amend_objective(self, objective_id: str, changes: dict, reason: str,
                        auth: OperatorAuthorization | None) -> OutcomeContract:
        cur, _ = self.store.objective(objective_id)
        forbidden = sorted(set(changes) & IMMUTABLE_OBJECTIVE_FIELDS)
        if forbidden:
            raise ControllerError(f"the original objective is immutable: cannot change {forbidden}")
        operator = self.authorizer.verify(auth, "amend_objective", objective_id,
                                          canonical_hash({"base": cur.hash(), "changes": changes}))
        data = cur.model_dump(mode="json") | changes | {"contract_version": cur.contract_version + 1}
        new = OutcomeContract.model_validate(data)
        self.store.insert_objective(new, approved_by=operator if cur.approval_status == "approved" else None,
                                    amendment_reason=reason, authorised_by=operator,
                                    max_active=self.config.limits.max_active_tasks_per_objective)
        return new

    def objective(self, objective_id: str) -> OutcomeContract:
        return self.store.objective(objective_id)[0]

    # ================================================================== planning (§6-8)
    def _limits(self, spec: TaskSpec) -> dict:
        L = self.config.limits
        def cap(value, maximum, name):
            if value is None:
                return maximum
            if value > maximum:
                raise ControllerError(f"{spec.task_id}: {name}={value} exceeds the trusted limit {maximum}")
            return value
        return {"maximum_actions": cap(spec.maximum_actions, L.max_actions_per_task, "maximum_actions"),
                "maximum_retries": cap(spec.maximum_retries, L.max_retries_per_step, "maximum_retries"),
                "maximum_failed_steps": cap(spec.maximum_failed_steps, L.max_failed_steps_per_task, "maximum_failed_steps"),
                "maximum_parallel_tools": cap(spec.maximum_parallel_tools, L.max_concurrent_tools_per_task,
                                              "maximum_parallel_tools"),
                "execution_deadline": cap(spec.execution_deadline, L.max_task_duration_s, "execution_deadline")}

    def _budget(self, spec: TaskSpec) -> ResourceBudget:
        base = dict(self.config.default_resource_budget)
        for k, v in (spec.resource_budget or {}).items():
            if k not in base:
                raise ControllerError(f"{spec.task_id}: unknown budget '{k}'")
            if v > base[k]:
                raise ControllerError(f"{spec.task_id}: budget {k}={v} exceeds the trusted default {base[k]}")
            base[k] = v
        return ResourceBudget.model_validate(base)

    def build_contract(self, objective: OutcomeContract, spec: TaskSpec, version: int = 1) -> TaskContract:
        req = ToolRequirement(task_type=spec.task_type, operations=tuple(spec.operations),
                              input_fields=tuple(spec.input_fields), resource_types=tuple(spec.resource_types),
                              prohibited_operations=tuple(spec.prohibited_operations), max_risk=spec.max_risk)
        alloc = self.allocator.allocate(req, spec.proposed_tool_ids)
        if alloc.status != "ALLOCATED":
            raise PlanningBlocked(f"{spec.task_id}: no suitable tool pool", details={
                "status": "BLOCKED", "task_id": spec.task_id, "missing": alloc.missing,
                "compatible_tools": alloc.candidates, "excluded": alloc.rejected})
        criteria = [TaskCriterion.model_validate(k) for k in spec.acceptance_criteria]
        c = TaskContract(
            task_id=spec.task_id, parent_objective_id=objective.objective_id, parent_task_id=spec.parent_task_id,
            contract_version=version, requirement_ids=spec.requirement_ids, task_type=spec.task_type,
            task_description=spec.description, required_outcome=spec.required_outcome,
            permitted_inputs=spec.permitted_inputs, input_checksums=spec.input_checksums,
            required_outputs=[OutputSpec.model_validate(o) for o in spec.required_outputs],
            output_schemas=spec.output_schemas, permitted_tool_ids=alloc.tool_ids,
            permitted_resource_ids=spec.permitted_resource_ids,
            permitted_filesystem_paths=spec.permitted_filesystem_paths,
            permitted_network_destinations=spec.permitted_network_destinations,
            permitted_operations=list(spec.operations), prohibited_operations=spec.prohibited_operations,
            dependency_task_ids=spec.dependency_task_ids, acceptance_criteria=criteria,
            verification_handler_ids=sorted({k.verifier for k in criteria}), resource_budget=self._budget(spec),
            approval_requirements=spec.approval_requirements, rollback_procedure=spec.rollback_procedure,
            assigned_worker=spec.assigned_worker, **self._limits(spec))
        return c.sealed()

    def validate_contract(self, c: TaskContract, objective: OutcomeContract, known_tasks: set[str]) -> list[str]:
        """Strict validation of any contract before it is stored (also used for externally built contracts)."""
        problems = []
        if c.compute_hash() != c.contract_hash:
            problems.append("contract_hash does not match the contract body")
        if c.parent_objective_id != objective.objective_id:
            problems.append("contract belongs to another objective")
        for n in c.permitted_tool_ids:
            t = self.catalogue.get(n)
            if t is None:
                problems.append(f"unknown tool identifier {n}")
            elif not t.available:
                problems.append(f"tool {n} is {t.availability}")
        limit = self.allocator.limit_for(c.task_type)
        if len(c.permitted_tool_ids) > limit:
            problems.append(f"{len(c.permitted_tool_ids)} tools exceed the limit of {limit} for task type {c.task_type}")
        unknown_reqs = set(c.requirement_ids) - objective.requirement_ids()
        if unknown_reqs:
            problems.append(f"requirements not in the objective: {sorted(unknown_reqs)}")
        for k in c.acceptance_criteria:
            if set(k.requirement_ids) - set(c.requirement_ids):
                problems.append(f"{k.id} references requirements outside the task")
            for tid in k.repair_tool_ids:
                if tid not in c.permitted_tool_ids:
                    problems.append(f"{k.id} repair tool {tid} is not in the task pool")
        missing_deps = set(c.dependency_task_ids) - known_tasks
        if missing_deps:
            problems.append(f"unknown dependency tasks {sorted(missing_deps)}")
        for name, maximum in (("maximum_actions", self.config.limits.max_actions_per_task),
                              ("maximum_parallel_tools", self.config.limits.max_concurrent_tools_per_task),
                              ("maximum_failed_steps", self.config.limits.max_failed_steps_per_task),
                              ("maximum_retries", self.config.limits.max_retries_per_step),
                              ("execution_deadline", self.config.limits.max_task_duration_s)):
            if getattr(c, name) > maximum:
                problems.append(f"{name}={getattr(c, name)} exceeds the trusted limit {maximum}")
        return problems

    @staticmethod
    def find_cycle(graph: dict[str, list[str]]) -> list[str] | None:
        state: dict[str, int] = {}
        stack: list[str] = []

        def visit(n):
            state[n] = 1
            stack.append(n)
            for d in graph.get(n, []):
                if state.get(d) == 1:
                    return stack[stack.index(d):] + [d]
                if state.get(d) is None and d in graph:
                    r = visit(d)
                    if r:
                        return r
            state[n] = 2
            stack.pop()
            return None
        for n in sorted(graph):
            if state.get(n) is None:
                r = visit(n)
                if r:
                    return r
        return None

    def plan(self, objective_id: str, specs: list[TaskSpec]) -> list[TaskContract]:
        objective = self.objective(objective_id)
        if objective.approval_status != "approved":
            raise ControllerError("tasks can only be planned for an approved objective")
        existing = {t["task_id"] for t in self.store.tasks(objective_id)}
        ids = [s.task_id for s in specs]
        if len(set(ids)) != len(ids) or set(ids) & existing:
            raise ControllerError("duplicate task identifiers")
        graph = {s.task_id: list(s.dependency_task_ids) for s in specs}
        cycle = self.find_cycle(graph)
        if cycle:
            raise ControllerError("circular task dependencies: " + " -> ".join(cycle), details={"cycle": cycle})
        contracts = [self.build_contract(objective, s) for s in specs]
        known = existing | set(ids)
        for c in contracts:
            problems = self.validate_contract(c, objective, known)
            if problems:
                raise ControllerError(f"{c.task_id} contract rejected", details=problems)
        for c in contracts:
            self._persist_new_task(c, "planned")
        uncovered = objective.requirement_ids() - {r for c in self._all_contracts(objective_id) for r in c.requirement_ids}
        if uncovered:
            log.warning("requirements without tasks: %s", sorted(uncovered))
        return contracts

    def submit_contract(self, objective_id: str, c: TaskContract) -> TaskContract:
        """Accept an externally built contract only if it passes the same strict validation."""
        objective = self.objective(objective_id)
        known = {t["task_id"] for t in self.store.tasks(objective_id)} | {c.task_id}
        graph = {cc.task_id: cc.dependency_task_ids for cc in self._all_contracts(objective_id)} | \
            {c.task_id: c.dependency_task_ids}
        cycle = self.find_cycle(graph)
        if cycle:
            raise ControllerError("circular task dependencies: " + " -> ".join(cycle))
        problems = self.validate_contract(c, objective, known)
        if problems:
            raise ControllerError(f"{c.task_id} contract rejected", details=problems)
        self._persist_new_task(c, "submitted")
        return c

    def _persist_new_task(self, c: TaskContract, reason: str) -> None:
        cid = self.controller_id
        self.store.insert_task(c, reason, cid)
        b = c.resource_budget
        self.store.init_budgets(c.parent_objective_id, c.task_id, {
            "actions": c.maximum_actions, "failed_steps": c.maximum_failed_steps,
            "concurrent_tools": c.maximum_parallel_tools, "repairs": self.config.limits.max_repairs_per_task,
            "invalid_proposals": self.config.limits.max_proposal_corrections + 1, "tokens": b.tokens,
            "cpu_seconds": b.cpu_seconds, "network_requests": b.network_requests,
            "external_api_calls": b.external_api_calls, "spend_cents": b.spend_cents})
        o, t = c.parent_objective_id, c.task_id
        self.store.transition(o, t, "VALIDATING", "contract stored", cid)
        self.store.transition(o, t, "PLANNING", "contract validated", cid)
        if c.approval_requirements:
            self.store.transition(o, t, "AWAITING_APPROVAL", f"needs {c.approval_requirements} (Stage 02)", cid)
        elif c.dependency_task_ids:
            self.store.transition(o, t, "WAITING_FOR_DEPENDENCY", f"waits for {c.dependency_task_ids}", cid)
        else:
            self.store.transition(o, t, "READY", "no dependencies", cid)

    def _all_contracts(self, objective_id: str) -> list[TaskContract]:
        return [self.store.contract(objective_id, t["task_id"]) for t in self.store.tasks(objective_id)]

    def change_tool_pool(self, objective_id: str, task_id: str, tool_ids: list[str], reason: str,
                         auth: OperatorAuthorization | None) -> TaskContract:
        """The only way a pool changes: operator-authorised NEW contract version, never during a running attempt."""
        task = self.store.task(objective_id, task_id)
        if task["state"] in ACTIVE_STATES:
            raise ControllerError("tool pools are immutable during an execution attempt")
        c = self.store.contract(objective_id, task_id)
        new = c.model_copy(update={"permitted_tool_ids": sorted(tool_ids), "contract_version": c.contract_version + 1,
                                   "contract_hash": ""}).sealed()
        self.authorizer.verify(auth, "change_tool_pool", f"{objective_id}/{task_id}", new.contract_hash)
        problems = self.validate_contract(new, self.objective(objective_id),
                                          {t["task_id"] for t in self.store.tasks(objective_id)})
        if problems:
            raise ControllerError("new pool rejected", details=problems)
        self.store.insert_task(new, f"tool pool changed: {reason}", self.controller_id)
        return new

    # ================================================================== Focus-4 (§10)
    def refresh_readiness(self, objective_id: str) -> None:
        tasks = {t["task_id"]: t for t in self.store.tasks(objective_id)}
        for tid, t in tasks.items():
            if t["state"] != "WAITING_FOR_DEPENDENCY":
                continue
            deps = self.store.contract(objective_id, tid).dependency_task_ids
            states = [tasks[d]["state"] for d in deps]
            if all(s == "COMPLETED" for s in states):
                self.store.transition(objective_id, tid, "READY", "all dependencies completed and verified",
                                      self.controller_id, expect=["WAITING_FOR_DEPENDENCY"])
            elif any(s in ("FAILED", "BLOCKED", "CANCELLED") for s in states):
                self.store.transition(objective_id, tid, "BLOCKED", "a mandatory dependency did not complete",
                                      self.controller_id, expect=["WAITING_FOR_DEPENDENCY"])

    def release(self, objective_id: str) -> list[str]:
        self.refresh_readiness(objective_id)
        ready = [t["task_id"] for t in self.store.tasks(objective_id) if t["state"] == "READY" and not t["repair_of"]]
        return self.store.reserve_slots(objective_id, ready, self.controller_id)

    # ================================================================== task execution
    def run_task(self, objective_id: str, task_id: str, source: ModelAdapter, *,
                 repair_evidence: list[dict] | None = None) -> str:
        cid = self.controller_id
        task = self.store.transition(objective_id, task_id, "RUNNING", "worker started", cid, expect=["QUEUED"])
        c = self.store.contract(objective_id, task_id)
        objective = self.objective(objective_id)
        ctx = self.gateway.issue_context(objective_id, task_id, task["attempt_id"], c.contract_hash, c.assigned_worker)
        tool_ops = {n: list(self.catalogue.get(n).spec.operations) for n in c.permitted_tool_ids if self.catalogue.get(n)}
        feedback: list[dict] | None = None
        recent: list[dict] = []
        deps = self._dependency_results(objective_id, c)
        while True:
            state = self.store.task(objective_id, task_id)["state"]
            if state != "RUNNING":
                return self._finish(objective_id, task_id, state)
            budgets = self.store.budgets(objective_id, task_id)
            try:
                packet = self.assembler.assemble(objective, c, state=state, budgets=budgets, dependency_results=deps,
                                                 recent_outputs=recent[-5:],
                                                 capacity_tokens=source.capabilities().context_tokens,
                                                 repair_evidence=repair_evidence)
            except ContextTooLarge as exc:
                return self._end(objective_id, task_id, "BLOCKED", f"context too large: {exc}")
            resp = source.propose(packet, feedback, timeout_s=60)
            if not self.store.consume(objective_id, task_id, "tokens", max(0, resp.tokens_used)):
                return self._end(objective_id, task_id, "BLOCKED", "token budget exhausted")
            parsed = parse_proposal(resp.raw, c, tool_ops)
            pid = parsed.proposal.proposal_id if parsed.proposal else new_id("req")
            self.store.record_proposal(objective_id, task_id, task["attempt_id"], pid,
                                       resp.raw if isinstance(resp.raw, (dict, list)) else {"raw": str(resp.raw)[:20000]},
                                       parsed.proposal is not None, parsed.errors, resp.source)
            if parsed.proposal is None:
                if not self.store.consume(objective_id, task_id, "invalid_proposals", 1) or \
                        self.store.budgets(objective_id, task_id)["invalid_proposals"]["remaining"] <= 0:
                    return self._end(objective_id, task_id, "BLOCKED",
                                     "the model repeatedly failed to produce a valid structured proposal",
                                     {"errors": parsed.errors})
                feedback = parsed.errors
                continue
            p = parsed.proposal
            if isinstance(p, CompletionClaim):
                outcome = self._handle_claim(objective_id, c, p, task["attempt_id"], source)
                if outcome is None:
                    feedback = [{"field": "claim", "message": "completion rejected: see drift violations",
                                 "permitted": ["continue with tool proposals"]}]
                    if not self.store.consume(objective_id, task_id, "invalid_proposals", 1) or \
                            self.store.budgets(objective_id, task_id)["invalid_proposals"]["remaining"] <= 0:
                        return self._end(objective_id, task_id, "BLOCKED", "repeated unsupported completion claims")
                    continue
                return outcome
            result = self.gateway.execute(ctx, p)
            if result.status in ("executed", "replayed"):
                feedback = None
                recent.append({"execution_id": result.execution_id, "tool_id": p.tool_id, "content": result.output})
            else:
                feedback = [{"field": v.field, "rule": v.rule, "message": v.detail, "permitted": list(v.permitted)}
                            for v in result.violations] or [{"field": "execution", "message": result.reason,
                                                            "output": result.output}]
                if result.status == "rejected":
                    if not self.store.consume(objective_id, task_id, "invalid_proposals", 1) or \
                            self.store.budgets(objective_id, task_id)["invalid_proposals"]["remaining"] <= 0:
                        if self.store.task(objective_id, task_id)["state"] == "RUNNING":
                            return self._end(objective_id, task_id, "BLOCKED", "too many rejected proposals",
                                             {"last": feedback})

    def _dependency_results(self, objective_id: str, c: TaskContract) -> list[dict]:
        out = []
        for dep in c.dependency_task_ids:
            for e in self.store.evidence_for(objective_id, dep):
                if e["kind"] == "verification" and e["content"].get("passed"):
                    out.append({"task_id": dep, "evidence_id": e["evidence_id"], "content": e["content"]})
        return out

    def _end(self, objective_id: str, task_id: str, state: str, reason: str, evidence: dict | None = None) -> str:
        self.store.transition(objective_id, task_id, state, reason, self.controller_id, evidence=evidence)
        return self._finish(objective_id, task_id, state)

    def _finish(self, objective_id: str, task_id: str, state: str) -> str:
        if state in ("COMPLETED", "FAILED", "BLOCKED", "CANCELLED"):
            self.store.unlock_resources(objective_id, task_id)
        return state

    def _handle_claim(self, objective_id: str, c: TaskContract, claim: CompletionClaim, attempt_id: str,
                      source: ModelAdapter) -> str | None:
        execs = [e for e in self.store.executions(objective_id, c.task_id) if e["attempt_id"] == attempt_id]
        rows = {eid: self.store.evidence(eid) for eid in claim.evidence_ids}
        violations = DriftGuard.check_completion(claim, c, execs, rows)
        if violations:
            for v in violations:
                self.store.record_drift(objective_id, c.task_id, claim.proposal_id, v.rule, f"{v.field}: {v.detail}")
            return None
        self.store.transition(objective_id, c.task_id, "VERIFYING", "completion claimed; independent verification",
                              self.controller_id, expect=["RUNNING"])
        return self._verify_and_close(objective_id, c, attempt_id, source)

    def _verify_and_close(self, objective_id: str, c: TaskContract, attempt_id: str, source: ModelAdapter) -> str:
        ws = self.gateway.workspace(objective_id)
        while True:
            results = self.verifier.verify(c, attempt_id, ws)
            if all(r.passed for r in results):
                self.store.transition(objective_id, c.task_id, "COMPLETED", "all acceptance criteria independently "
                                      "verified", self.controller_id, expect=["VERIFYING"])
                return self._finish(objective_id, c.task_id, "COMPLETED")
            if any(r.pending_human for r in results if not r.passed) and \
                    all(r.passed or r.pending_human for r in results):
                return self._end(objective_id, c.task_id, "BLOCKED", "awaiting independent human acceptance")
            failed = [r for r in results if not r.passed]
            if not self.store.reserve(objective_id, c.task_id, "repairs", 1):
                return self._end(objective_id, c.task_id, "FAILED", "repair budget exhausted; unresolved criteria: " +
                                 ", ".join(r.criterion_id for r in failed),
                                 {"unresolved": [{"criterion_id": r.criterion_id, "detail": r.detail} for r in failed]})
            self.store.settle(objective_id, c.task_id, "repairs", 1, 1)
            self.store.transition(objective_id, c.task_id, "RETRYING", "verification failed: bounded repair",
                                  self.controller_id, expect=["VERIFYING"],
                                  evidence={"failed": [r.criterion_id for r in failed]})
            child_state = self._run_repair(objective_id, c, failed, source)
            # Whatever the repair task's outcome, the ORIGINAL criteria are re-verified independently; another bounded
            # repair follows only while the parent's repair budget lasts.
            self.store.transition(objective_id, c.task_id, "RUNNING", f"repair task ended {child_state}; re-verifying",
                                  self.controller_id, expect=["RETRYING"])
            self.store.transition(objective_id, c.task_id, "VERIFYING", "re-verification after repair",
                                  self.controller_id, expect=["RUNNING"])

    def _next_task_id(self, objective_id: str) -> str:
        nums = [int(t["task_id"].split("-")[1]) for t in self.store.tasks(objective_id)]
        return f"TASK-{max(nums, default=0) + 1:04d}"

    def _run_repair(self, objective_id: str, c: TaskContract, failed: list, source: ModelAdapter) -> str:
        crit = [k for k in c.acceptance_criteria if k.id in {r.criterion_id for r in failed}]
        tools = sorted({t for k in crit for t in k.repair_tool_ids}) or list(c.permitted_tool_ids)
        budgets = self.store.budgets(objective_id, c.task_id)
        child = c.model_copy(update={
            "task_id": self._next_task_id(objective_id), "parent_task_id": c.task_id, "contract_version": 1,
            "requirement_ids": sorted({r for k in crit for r in k.requirement_ids}),
            "task_description": f"REPAIR of {c.task_id}: satisfy {[k.id for k in crit]} without changing other outputs",
            "permitted_tool_ids": tools, "acceptance_criteria": crit, "dependency_task_ids": [],
            "verification_handler_ids": sorted({k.verifier for k in crit}),
            "maximum_actions": max(1, int(min(c.maximum_actions, budgets["actions"]["remaining"] or 1))),
            "contract_hash": ""}).sealed()
        problems = self.validate_contract(child, self.objective(objective_id),
                                          {t["task_id"] for t in self.store.tasks(objective_id)} | {child.task_id})
        if problems:
            return "BLOCKED"
        self.store.insert_task(child, f"bounded repair of {c.task_id}", self.controller_id)
        self.store.init_budgets(objective_id, child.task_id, {
            "actions": child.maximum_actions, "failed_steps": child.maximum_failed_steps,
            "concurrent_tools": child.maximum_parallel_tools, "repairs": 0,
            "invalid_proposals": self.config.limits.max_proposal_corrections + 1, "tokens": child.resource_budget.tokens,
            "cpu_seconds": child.resource_budget.cpu_seconds, "network_requests": 0, "external_api_calls": 0,
            "spend_cents": 0})
        with self.store.db.tx() as x:
            x.execute("UPDATE control.tasks SET repair_of = %s WHERE objective_id = %s AND task_id = %s",
                      (c.task_id, objective_id, child.task_id))
        for s in ("VALIDATING", "PLANNING", "READY"):
            self.store.transition(objective_id, child.task_id, s, "repair task", self.controller_id)
        self.store.transition(objective_id, child.task_id, "QUEUED", "repair runs in the parent's Focus-4 slot",
                              self.controller_id, new_attempt=True)
        evidence = [{"criterion_id": r.criterion_id, "detail": r.detail} for r in failed]
        return self.run_task(objective_id, child.task_id, source, repair_evidence=evidence)

    # ================================================================== cancellation
    def cancel_task(self, objective_id: str, task_id: str, reason: str = "cancelled") -> str:
        for e in self.store.executions(objective_id, task_id):
            if e["state"] in ("reserved", "running"):
                self.gateway.cancel(e["execution_id"])
        self.store.transition(objective_id, task_id, "CANCELLED", reason, self.controller_id)
        self.store.add_evidence(objective_id, task_id, "cancellation", {"reason": reason, "at": iso_utc()})
        self.store.release_slot(objective_id, task_id)
        return self._finish(objective_id, task_id, "CANCELLED")

    def reattempt(self, objective_id: str, task_id: str, auth: OperatorAuthorization | None) -> str:
        c = self.store.contract(objective_id, task_id)
        self.authorizer.verify(auth, "reattempt_task", f"{objective_id}/{task_id}", c.contract_hash)
        if not self.store.requeue_with_slot(objective_id, task_id, self.controller_id, "operator-authorised new attempt"):
            raise ControllerError("no Focus-4 slot free for the new attempt")
        return "QUEUED"

    # ================================================================== objective loop, integration, checkpoints
    def run_objective(self, objective_id: str, sources: dict[str, ModelAdapter], max_batches: int = 50) -> dict:
        objective = self.objective(objective_id)
        done_checks: set[str] = set()
        batches = []
        exhausted = True
        for _ in range(max_batches):
            released = self.release(objective_id)
            if not released:
                exhausted = False
                break
            with ThreadPoolExecutor(max_workers=len(released)) as pool:
                states = dict(zip(released, pool.map(lambda t: self.run_task(objective_id, t, sources[t]), released)))
            completed = {t["task_id"] for t in self.store.tasks(objective_id) if t["state"] == "COMPLETED"}
            checks = [k for k in objective.integration_checks if k.id not in done_checks and set(k.after_tasks) <= completed]
            results = self._integrate(objective_id, checks)
            done_checks |= {k.id for k in checks}
            ok = all(r["passed"] for r in results)
            cp = self.checkpoint(objective_id, verified=ok, integration=results)
            batches.append({"released": released, "states": states, "integration": results, "checkpoint": cp})
            for t in released:
                self.store.release_slot(objective_id, t)
            if not ok:
                self.store.set_objective_status(objective_id, "blocked", batch=len(batches))
                return {"status": "blocked", "reason": "integration verification failed", "batches": batches}
            self.store.set_objective_status(objective_id, "open", batch=len(batches))
        if exhausted:   # batch limit for this call reached: more work remains, nothing is finalised
            return {"status": "in_progress", "batches": batches}
        report = self.final_verification(objective_id)
        return {"status": report["status"], "batches": batches, "final": report}

    def _integrate(self, objective_id: str, checks) -> list[dict]:
        ws = self.gateway.workspace(objective_id)
        executions = self.store.executions(objective_id)
        out = []
        for k in checks:
            crit = TaskCriterion.model_construct(id=k.id, description=k.description, requirement_ids=k.requirement_ids,
                                                 verifier=k.verifier, params=k.params, repair_tool_ids=[])
            try:
                passed, _, detail = self.verifier._check(crit, None, ws, executions)
            except Exception as exc:  # noqa: BLE001
                passed, detail = False, {"error": str(exc)}
            eid = self.store.add_evidence(objective_id, None, "integration", {"check": k.id, "passed": passed,
                                                                             "detail": detail})
            self.store.add_verification(objective_id, "INTEGRATION", None, k.id, passed, detail, eid,
                                        IndependentVerifier.NAME)
            out.append({"check": k.id, "passed": passed, "evidence_id": eid, "detail": detail})
        return out

    def checkpoint(self, objective_id: str, *, verified: bool, integration: list[dict] | None = None) -> dict:
        ws = self.gateway.workspace(objective_id)
        tasks = self.store.tasks(objective_id)
        completed, pending, failures, versions, budgets = [], [], [], {}, {}
        for t in tasks:
            c = self.store.contract(objective_id, t["task_id"])
            versions[t["task_id"]] = {"version": c.contract_version, "hash": c.contract_hash}
            budgets[t["task_id"]] = {k: v["remaining"] for k, v in self.store.budgets(objective_id, t["task_id"]).items()}
            if t["state"] == "COMPLETED":
                outputs = {}
                for o in c.required_outputs:
                    if o.path and (ws / o.path).is_file():
                        outputs[o.id] = {"path": o.path,
                                         "sha256": hashlib.sha256((ws / o.path).read_bytes()).hexdigest()}
                completed.append({"task_id": t["task_id"], "attempt_id": t["attempt_id"], "outputs": outputs})
            elif t["state"] in ("FAILED", "BLOCKED"):
                failures.append({"task_id": t["task_id"], "state": t["state"]})
            else:
                pending.append({"task_id": t["task_id"], "state": t["state"]})
        data = {"objective_id": objective_id, "objective_version": self.objective(objective_id).contract_version,
                "completed": completed, "pending": pending, "unresolved_failures": failures,
                "contract_versions": versions, "remaining_budgets": budgets, "integration": integration or [],
                "recovery": {"workspace": str(ws), "resume": "tasks in active states move to RECOVERING then QUEUED; "
                             "uncertain external operations are never repeated automatically"}, "created_at": iso_utc()}
        return self.store.add_checkpoint(objective_id, verified, data)

    def final_verification(self, objective_id: str) -> dict:
        report = trace(self.store, objective_id)
        tasks = self.store.tasks(objective_id)
        all_done = all(t["state"] == "COMPLETED" for t in tasks)
        status = "completed" if report["complete"] and all_done else "incomplete"
        self.store.set_objective_status(objective_id, "completed" if status == "completed" else "blocked")
        self.store.add_evidence(objective_id, None, "final_verification", {"status": status, "trace": report})
        return {"status": status, "trace": report}

    # ================================================================== recovery (§19)
    def recover(self, objective_id: str) -> dict:
        """After a restart: restore from persistent state; never repeat an uncertain external operation."""
        actions = []
        latest = self.store.latest_checkpoint(objective_id)
        ws = self.gateway.workspace(objective_id)
        if latest:
            for t in latest["data"]["completed"]:
                for oid, o in t["outputs"].items():
                    p = ws / o["path"]
                    ok = p.is_file() and hashlib.sha256(p.read_bytes()).hexdigest() == o["sha256"]
                    actions.append({"check_output": f"{t['task_id']}/{oid}", "intact": ok})
        for e in self.store.executions(objective_id):
            if e["state"] in ("reserved", "running"):
                if e["idempotency"] == "external_uncertain":
                    new_state = "outcome_unknown"
                else:
                    new_state = "failed"   # the supervisor kills local handlers when the controller dies
                self.store.finish_execution(e["execution_id"], new_state,
                                            error={"reason": "controller restarted during execution"})
                actions.append({"execution": e["execution_id"], "state": new_state})
        for t in self.store.tasks(objective_id):
            if t["state"] not in ACTIVE_STATES:
                continue
            unknown = [e for e in self.store.executions(objective_id, t["task_id"])
                       if e["state"] == "outcome_unknown" and e["attempt_id"] == t["attempt_id"]]
            self.store.transition(objective_id, t["task_id"], "RECOVERING", "controller restart", self.controller_id)
            if unknown:
                self.store.transition(objective_id, t["task_id"], "BLOCKED", "external outcome uncertain: "
                                      "reconciliation required before any retry", self.controller_id,
                                      evidence={"executions": [e["execution_id"] for e in unknown]})
                actions.append({"task": t["task_id"], "state": "BLOCKED"})
            else:
                self.store.transition(objective_id, t["task_id"], "QUEUED", "resumed after restart", self.controller_id,
                                      new_attempt=True)
                actions.append({"task": t["task_id"], "state": "QUEUED"})
        self.store.add_recovery(objective_id, "recover", {"checkpoint": latest["checkpoint_id"] if latest else None,
                                                          "actions": actions})
        return {"checkpoint": latest["checkpoint_id"] if latest else None, "actions": actions}

    def reconcile(self, objective_id: str, execution_id: str) -> dict:
        """Ask the tool's reconcile handler (isolated) what actually happened externally. Never re-executes."""
        e = next(x for x in self.store.executions(objective_id) if x["execution_id"] == execution_id)
        if e["state"] != "outcome_unknown":
            return {"execution_id": execution_id, "state": e["state"], "reconciled": False}
        t = self.catalogue.get(e["tool_number"])
        reg = self.registry.get(t.spec.capability_id)
        if "reconcile" not in reg.manifest.handler_refs:
            return {"execution_id": execution_id, "state": "outcome_unknown", "reconciled": False,
                    "reason": "no reconcile handler: operator resolution required"}
        ticket = self.runtime.issue_ticket(new_id("exe"), t.spec.capability_id, "reconcile")
        r = self.runtime.run(ticket=ticket, mode="reconcile", capability_id=t.spec.capability_id,
                             package_dir=reg.manifest.directory, expected_digest=reg.package_digest,
                             handlers=reg.manifest.handler_refs, timeout_s=30, payload=e["arguments"],
                             context={"execution_id": execution_id}, workdir=self.gateway.workspace(objective_id))
        self.store.add_recovery(objective_id, "reconcile", {"execution_id": execution_id, "status": r.status,
                                                            "value": r.value})
        return {"execution_id": execution_id, "state": "outcome_unknown", "reconciled": r.status == "ok",
                "observed": r.value}

    # ================================================================== status
    def status(self, objective_id: str) -> dict:
        return {"objective": self.objective(objective_id).model_dump(mode="json"),
                "runtime": self.store.objective_runtime(objective_id),
                "tasks": self.store.tasks(objective_id), "active_slots": self.store.active_count(objective_id)}
