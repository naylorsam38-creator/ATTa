"""Outcome contracts, task contracts and execution proposals (Stage 01A §4, §8, §12) — strict schemas.

All models forbid unknown fields. Hashes are SHA-256 over canonical JSON, so a contract cannot change without
its hash changing. These models are DATA: only the controller creates or versions them."""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from backend.contracts import canonical_hash

REQ_ID = r"^(FR|NFR)-[0-9]{2,}$"
TASK_ID = r"^TASK-[0-9]{4,}$"
TOOL_NUMBER = r"^(0[0-3][0-9]|040)\.[0-9]{3}$"
VERIFIERS = frozenset({"output_exists", "file_sha256", "json_schema", "text_contains", "file_format", "tool_verify",
                       "execution_succeeded", "human_review"})


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Requirement(_Strict):
    id: str = Field(pattern=REQ_ID)
    text: str = Field(min_length=3, max_length=4000)


class Deliverable(_Strict):
    id: str = Field(pattern=r"^DEL-[0-9]{2,}$")
    description: str = Field(min_length=3)
    requirement_ids: list[str] = Field(min_length=1)


class ObjectiveCriterion(_Strict):
    id: str = Field(pattern=r"^AC-[0-9]{2,}$")
    description: str = Field(min_length=3)
    requirement_ids: list[str] = Field(min_length=1)


class IntegrationCheck(_Strict):
    """Objective-level integration verification run after the tasks it names are completed (Focus-4 batches)."""
    id: str = Field(pattern=r"^IC-[0-9]{2,}$")
    description: str = Field(min_length=3)
    after_tasks: list[str] = Field(min_length=1)
    requirement_ids: list[str] = Field(min_length=1)
    verifier: str
    params: dict[str, Any] = Field(default_factory=dict)


class OutcomeContract(_Strict):
    objective_id: str = Field(pattern=r"^obj_[0-9A-HJKMNP-TV-Z]{26}$")
    original_instructions: str = Field(min_length=1)
    original_blueprint: str | None = None
    original_source_sha256: str = Field(min_length=64, max_length=64)
    required_deliverables: list[Deliverable] = Field(min_length=1)
    functional_requirements: list[Requirement] = Field(min_length=1)
    non_functional_requirements: list[Requirement] = Field(default_factory=list)
    restrictions: list[str] = Field(default_factory=list)
    required_capabilities: list[str] = Field(default_factory=list)   # capability numbers, e.g. "014"
    acceptance_criteria: list[ObjectiveCriterion] = Field(min_length=1)
    approved_decisions: list[str] = Field(default_factory=list)
    dependencies: list[str] = Field(default_factory=list)
    completion_conditions: list[str] = Field(min_length=1)
    integration_checks: list[IntegrationCheck] = Field(default_factory=list)
    contract_version: int = Field(ge=1)
    created_at: str
    approval_status: Literal["draft", "approved"]

    def requirement_ids(self) -> set[str]:
        return {r.id for r in self.functional_requirements + self.non_functional_requirements}

    @model_validator(mode="after")
    def _refs(self):
        ids = self.requirement_ids()
        for item in list(self.required_deliverables) + list(self.acceptance_criteria) + list(self.integration_checks):
            unknown = set(item.requirement_ids) - ids
            if unknown:
                raise ValueError(f"{item.id} references unknown requirements {sorted(unknown)}")
        return self

    def hash(self) -> str:
        return canonical_hash(self.model_dump(mode="json"))


class OutputSpec(_Strict):
    id: str = Field(pattern=r"^OUT-[0-9]{2,}$")
    path: str | None = None
    description: str = Field(min_length=3)


class TaskCriterion(_Strict):
    id: str = Field(pattern=r"^TC-[0-9]{2,}$")
    description: str = Field(min_length=3)
    requirement_ids: list[str] = Field(min_length=1)
    verifier: str
    params: dict[str, Any] = Field(default_factory=dict)
    repair_tool_ids: list[str] = Field(default_factory=list)

    @field_validator("verifier")
    @classmethod
    def _known(cls, v):
        if v not in VERIFIERS:
            raise ValueError(f"unknown verifier '{v}' (known: {sorted(VERIFIERS)})")
        return v


class ResourceBudget(_Strict):
    tokens: int = Field(ge=0)
    cpu_seconds: float = Field(ge=0)
    memory_mb: int = Field(ge=64)
    disk_mb: float = Field(ge=0)
    network_requests: int = Field(ge=0)
    external_api_calls: int = Field(ge=0)
    spend_cents: int = Field(ge=0)


TaskState = Literal["CREATED", "VALIDATING", "PLANNING", "READY", "AWAITING_APPROVAL", "QUEUED", "RUNNING",
                    "WAITING_FOR_DEPENDENCY", "RETRYING", "VERIFYING", "COMPLETED", "FAILED", "BLOCKED", "CANCELLED",
                    "RECOVERING"]


class TaskContract(_Strict):
    """The 31-field machine-readable execution contract of one task (Stage 01A §8)."""
    task_id: str = Field(pattern=TASK_ID)
    parent_objective_id: str
    parent_task_id: str | None = None
    contract_version: int = Field(ge=1)
    contract_hash: str = ""
    requirement_ids: list[str] = Field(min_length=1)
    task_type: str = Field(pattern=r"^[a-z][a-z0-9_.]*$")
    task_description: str = Field(min_length=3)
    required_outcome: str = Field(min_length=3)
    permitted_inputs: list[str] = Field(default_factory=list)
    input_checksums: dict[str, str] = Field(default_factory=dict)
    required_outputs: list[OutputSpec] = Field(default_factory=list)
    output_schemas: dict[str, dict] = Field(default_factory=dict)
    permitted_tool_ids: list[str] = Field(min_length=1)
    permitted_resource_ids: list[str] = Field(default_factory=list)
    permitted_filesystem_paths: list[str] = Field(default_factory=list)
    permitted_network_destinations: list[str] = Field(default_factory=list)
    permitted_operations: list[str] = Field(min_length=1)
    prohibited_operations: list[str] = Field(default_factory=list)
    dependency_task_ids: list[str] = Field(default_factory=list)
    acceptance_criteria: list[TaskCriterion] = Field(min_length=1)
    verification_handler_ids: list[str] = Field(min_length=1)
    maximum_actions: int = Field(ge=1)
    maximum_retries: int = Field(ge=0)
    maximum_failed_steps: int = Field(ge=1)
    maximum_parallel_tools: int = Field(ge=1)
    execution_deadline: float = Field(gt=0, description="seconds allowed from first RUNNING")
    resource_budget: ResourceBudget
    approval_requirements: list[str] = Field(default_factory=list)
    rollback_procedure: str = Field(min_length=3)
    assigned_worker: str = Field(min_length=1)
    task_status: TaskState = "CREATED"

    @field_validator("permitted_tool_ids")
    @classmethod
    def _tool_numbers(cls, v):
        import re
        bad = [t for t in v if not re.match(TOOL_NUMBER, t)]
        if bad:
            raise ValueError(f"tool identifiers must be permanent numbers CCC.TTT: {bad}")
        if len(set(v)) != len(v):
            raise ValueError("duplicate tool identifiers in pool")
        return v

    @field_validator("permitted_filesystem_paths")
    @classmethod
    def _paths(cls, v):
        for p in v:
            if p.startswith(("/", "\\")) or ":" in p or ".." in p.replace("\\", "/").split("/"):
                raise ValueError(f"filesystem scope must be workspace-relative without '..': {p!r}")
        return v

    @model_validator(mode="after")
    def _consistency(self):
        if self.task_id in self.dependency_task_ids:
            raise ValueError("a task cannot depend on itself")
        unknown = {c.verifier for c in self.acceptance_criteria} - set(self.verification_handler_ids)
        if unknown:
            raise ValueError(f"criteria use verifiers not listed in verification_handler_ids: {sorted(unknown)}")
        clash = set(self.permitted_operations) & set(self.prohibited_operations)
        if clash:
            raise ValueError(f"operations both permitted and prohibited: {sorted(clash)}")
        return self

    def body(self) -> dict:
        d = self.model_dump(mode="json")
        d.pop("contract_hash")
        return d

    def compute_hash(self) -> str:
        return canonical_hash(self.body())

    def sealed(self) -> "TaskContract":
        return self.model_copy(update={"contract_hash": self.compute_hash()})


class ToolProposal(_Strict):
    """Stage 01A §12: the only form in which a model may ask for an action."""
    proposal_id: str = Field(pattern=r"^[A-Za-z0-9_-]{4,80}$")
    task_id: str = Field(pattern=TASK_ID)
    contract_version: int = Field(ge=1)
    tool_id: str = Field(pattern=TOOL_NUMBER)
    operation: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    arguments: dict[str, Any]
    target_resources: list[str] = Field(default_factory=list)
    expected_result: str = Field(min_length=1, max_length=2000)
    referenced_requirement_ids: list[str] = Field(min_length=1)


class CompletionClaim(_Strict):
    """A model may CLAIM completion; only independent verification can complete a task."""
    proposal_id: str = Field(pattern=r"^[A-Za-z0-9_-]{4,80}$")
    task_id: str = Field(pattern=TASK_ID)
    contract_version: int = Field(ge=1)
    claim: Literal["complete"]
    evidence_ids: list[str] = Field(default_factory=list)
