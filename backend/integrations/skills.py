"""Skills and reusable scripts (Stage 12 §18-§20).

A skill is DECLARATIVE: instructions, templates, schemas, workflows, tool references, validation rules, examples.
It never receives tools. Its tool references are resolved against the ONE numbered catalogue, and a task that uses
a skill still gets its pool from the Stage 01A allocator (≤ the configured maximum); the skill can only PROPOSE
tool numbers, which the allocator validates against the compatible tools.

Versioning: every version's content is stored immutably (integrations.skill_versions). A task pins a version by
binding `skill:<id>@<version>` -> content digest into its contract's input_checksums, so the pin is covered by the
contract hash; an upgrade never changes what an active task executes.

Reusable scripts: a workflow of kind 'script' is a parameterised, schema-validated, versioned sequence of tool
steps. ScriptProposalSource turns it into ToolProposals that run through the controller exactly like model
proposals (contract, drift guard, policy, approvals, budgets, gateway, independent verification). It is NOT a model
and NOT a second execution engine."""
from __future__ import annotations

import json
import re
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from jsonschema import Draft202012Validator

from backend.contracts import canonical_hash
from backend.orchestration.models import ModelCapabilities, ModelIdentity, ProposalResponse

from .packaging import capability_id_for

_PARAM = re.compile(r"\{\{\s*([a-z][a-z0-9_]*)\s*\}\}")


class SkillError(ValueError):
    pass


def resolve_reference(ref: str, catalogue) -> str | None:
    """'CCC.TTT' or '<capability_id>:<tool>' or '<integration_id>:<tool>' -> permanent tool number (or None)."""
    if re.fullmatch(r"(0[0-3][0-9]|040)\.[0-9]{3}", ref):
        return ref if catalogue.get(ref) is not None else None
    cap, _, tool = ref.partition(":")
    for candidate in (f"{cap}:{tool}", f"{capability_id_for(cap)}:{tool}"):
        for t in catalogue.all():
            if t.spec.string_id == candidate:
                return t.number
    return None


@dataclass(frozen=True)
class SkillContent:
    skill_id: str
    version: str
    integration_id: str
    instructions: str
    templates: dict
    schemas: dict
    tool_references: dict          # reference -> permanent tool number
    resource_references: list
    workflows: list
    validation_rules: list
    examples: list
    content_digest: str = ""

    def body(self) -> dict:
        d = dict(self.__dict__)
        d.pop("content_digest")
        return d

    @staticmethod
    def from_row(row: dict) -> "SkillContent":
        c = row["content"]
        return SkillContent(**c, content_digest=row["content_digest"])


def build_skill_content(manifest_raw: dict, package_dir: Path, catalogue) -> tuple[SkillContent | None, list[str]]:
    sk = manifest_raw["skill"]
    problems, resolved = [], {}
    for ref in sk["tool_references"]:
        n = resolve_reference(ref, catalogue)
        if n is None:
            problems.append(f"skill tool reference '{ref}' does not resolve to a numbered catalogue tool")
        else:
            resolved[ref] = n
    if problems:
        return None, problems
    instructions = (package_dir / sk["instructions"]).read_text(encoding="utf-8")
    templates = {t: (package_dir / t).read_text(encoding="utf-8") for t in sk.get("templates") or []}
    body = dict(skill_id=sk["skill_id"], version=manifest_raw["version"], integration_id=manifest_raw["integration_id"],
                instructions=instructions, templates=templates, schemas=sk.get("schemas") or {},
                tool_references=resolved, resource_references=list(sk.get("resource_references") or []),
                workflows=list(sk.get("workflows") or []), validation_rules=list(sk.get("validation_rules") or []),
                examples=list(sk.get("examples") or []))
    return SkillContent(**body, content_digest=canonical_hash(body)), []


def pin_key(skill_id: str, version: str) -> str:
    return f"skill:{skill_id}@{version}"


def _substitute(value, params: dict):
    if isinstance(value, str):
        m = _PARAM.fullmatch(value)
        if m:
            return params[m.group(1)]
        return _PARAM.sub(lambda mm: str(params[mm.group(1)]), value)
    if isinstance(value, dict):
        return {k: _substitute(v, params) for k, v in value.items()}
    if isinstance(value, list):
        return [_substitute(v, params) for v in value]
    return value


@dataclass
class ScriptProposalSource:
    """Deterministic proposal source for a pinned script version. NOT a model: responses are labelled
    source='atta-script' and every proposal is validated by the controller like any other."""
    skill: SkillContent
    workflow_id: str
    task_id: str
    requirement_ids: list[str]
    parameters: dict
    contract_version: int = 1
    _queue: list = field(default_factory=list)

    def __post_init__(self):
        wf = next((w for w in self.skill.workflows if w["id"] == self.workflow_id), None)
        if wf is None:
            raise SkillError(f"workflow '{self.workflow_id}' not in skill {self.skill.skill_id}@{self.skill.version}")
        errs = [e.message for e in Draft202012Validator(wf["parameters"]).iter_errors(self.parameters)]
        if errs:
            raise SkillError(f"script parameters invalid: {errs[:3]}")
        for k in self.parameters:
            if not re.fullmatch(r"[a-z][a-z0-9_]*", k):
                raise SkillError(f"invalid parameter name {k!r}")
        for i, step in enumerate(wf["steps"]):
            number = self.skill.tool_references.get(step["tool"])
            if number is None:
                raise SkillError(f"step {i} tool '{step['tool']}' is not a resolved reference of this skill version")
            self._queue.append({
                "proposal_id": f"scr-{uuid.uuid4().hex[:12]}", "task_id": self.task_id,
                "contract_version": self.contract_version, "tool_id": number, "operation": step["operation"],
                "arguments": _substitute(step["arguments"], self.parameters),
                "target_resources": _substitute(step.get("target_resources") or [], self.parameters),
                "expected_result": f"script {self.workflow_id} step {i + 1}",
                "referenced_requirement_ids": list(self.requirement_ids)})
        self._queue.append({"proposal_id": f"scr-{uuid.uuid4().hex[:12]}-done", "task_id": self.task_id,
                            "contract_version": self.contract_version, "claim": "complete", "evidence_ids": []})

    def identity(self) -> ModelIdentity:
        return ModelIdentity("atta-script", f"{self.skill.skill_id}:{self.workflow_id}", self.skill.version)

    def available(self) -> bool:
        return True

    def capabilities(self) -> ModelCapabilities:
        return ModelCapabilities(1_000_000, True, False)

    def propose(self, packet, feedback, timeout_s: float) -> ProposalResponse:
        if not self._queue:
            return ProposalResponse(raw="", source="atta-script", meta={"exhausted": True})
        return ProposalResponse(raw=json.dumps(self._queue.pop(0)), source="atta-script")

    def cancel(self) -> None:
        self._queue.clear()
