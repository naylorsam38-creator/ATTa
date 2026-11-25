"""Deterministic drift guard (Stage 01A §11) — runs outside the model, before every action.

Structural rules only: schemas, allow-lists, resource identifiers, paths, destinations, state, budgets and
execution history. It does not claim to judge semantic relevance; contracts that need that set
`approval_requirements`/`human_review` criteria, and a semantic review can never override a denial here."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

from jsonschema import Draft202012Validator

from backend.contracts import canonical_hash

from .catalogue import CatalogueTool
from .contracts import CompletionClaim, TaskContract, ToolProposal

PROTECTED_RESOURCE_PREFIXES = ("objective:", "contract:", "criteria:", "verifier:", "budget:", "policy:", "pool:",
                               "controller:")
READ_ONLY_OPERATIONS = frozenset({"read", "validate", "verify", "inspect", "count", "list"})


@dataclass(frozen=True)
class Violation:
    rule: str
    field: str
    detail: str
    permitted: tuple = ()


def resolve_path(value: str, workspace: Path, permitted: list[str], protected_prefixes: list[str],
                 repo_root: Path | None = None) -> tuple[Path | None, str | None]:
    """Resolve a workspace-relative path; refuse absolute paths, traversal, symlink escapes and unpermitted scope."""
    if not isinstance(value, str) or not value or value.startswith(("/", "\\", "~")) or ":" in value:
        return None, "paths must be workspace-relative (no absolute paths, drives or home references)"
    parts = value.replace("\\", "/").split("/")
    if ".." in parts:
        return None, "path traversal ('..') is not permitted"
    root = workspace.resolve()
    target = (root / value).resolve()
    if root != target and root not in target.parents:
        return None, "path resolves outside the task workspace (symlink escape?)"
    rel = target.relative_to(root).as_posix()
    for pp in protected_prefixes:
        if rel == pp or rel.startswith(pp.rstrip("/") + "/"):
            return None, f"path is inside protected area '{pp}'"
    if not any(rel == p.rstrip("/") or rel.startswith(p.rstrip("/") + "/") or p in ("", ".") for p in permitted):
        return None, f"path '{rel}' is outside the permitted filesystem scope {permitted}"
    return target, None


class DriftGuard:
    def __init__(self, no_progress_window: int = 3, protected_path_prefixes: list[str] | None = None):
        self.window = no_progress_window
        self.protected = protected_path_prefixes or []

    def check_proposal(self, p: ToolProposal, c: TaskContract, tool: CatalogueTool | None, workspace: Path,
                       history: list[dict], failures_for_step: int, max_retries: int) -> tuple[list[Violation], dict]:
        """Return (violations, resolved_arguments). resolved_arguments has validated absolute paths substituted."""
        v: list[Violation] = []
        args = dict(p.arguments)
        if p.task_id != c.task_id:
            v.append(Violation("task_mismatch", "task_id", f"proposal targets {p.task_id}, this is {c.task_id}"))
        if p.contract_version != c.contract_version:
            v.append(Violation("contract_version_mismatch", "contract_version",
                               f"proposal uses v{p.contract_version}; current contract is v{c.contract_version}"))
        if tool is None:
            v.append(Violation("unknown_tool", "tool_id", f"{p.tool_id} is not a registered tool",
                               tuple(c.permitted_tool_ids)))
            return v, args
        if p.tool_id not in c.permitted_tool_ids:
            v.append(Violation("tool_not_in_pool", "tool_id", f"{p.tool_id} is not in this task's tool pool",
                               tuple(c.permitted_tool_ids)))
            return v, args
        if p.operation not in tool.spec.operations:
            v.append(Violation("unsupported_operation", "operation", f"{p.tool_id} does not support '{p.operation}'",
                               tuple(tool.spec.operations)))
        if p.operation not in c.permitted_operations or p.operation in c.prohibited_operations:
            v.append(Violation("operation_not_permitted", "operation", f"'{p.operation}' is not permitted by the contract",
                               tuple(c.permitted_operations)))
        bad_reqs = sorted(set(p.referenced_requirement_ids) - set(c.requirement_ids))
        if bad_reqs:
            v.append(Violation("unrelated_requirement", "referenced_requirement_ids",
                               f"requirements {bad_reqs} are not assigned to this task", tuple(c.requirement_ids)))
        errs = [e.message for e in Draft202012Validator(tool.spec.input_schema).iter_errors(args)]
        if errs:
            v.append(Violation("invalid_arguments", "arguments", "; ".join(errs[:5])))
        for r in p.target_resources:
            if r.startswith(PROTECTED_RESOURCE_PREFIXES):
                v.append(Violation("protected_controller_state", "target_resources",
                                   f"'{r}' is controller state (objective, contract, criteria, verifier, budget, policy "
                                   "or tool pool) and can never be modified by a task"))
            elif r not in c.permitted_resource_ids:
                v.append(Violation("resource_not_permitted", "target_resources", f"resource '{r}' is not permitted",
                                   tuple(c.permitted_resource_ids)))
        for name in tool.spec.path_arguments:
            if name in args:
                resolved, why = resolve_path(args[name], workspace, c.permitted_filesystem_paths, self.protected)
                if why:
                    v.append(Violation("path_not_permitted", f"arguments.{name}", why, tuple(c.permitted_filesystem_paths)))
                else:
                    args[name] = str(resolved)
        for name in tool.spec.url_arguments:
            if name in args:
                host = urlparse(str(args[name])).hostname or ""
                if urlparse(str(args[name])).scheme not in ("https", "http") or host not in c.permitted_network_destinations:
                    v.append(Violation("network_not_permitted", f"arguments.{name}", f"destination '{host}' is not permitted",
                                       tuple(c.permitted_network_destinations)))
        if failures_for_step > max_retries:
            v.append(Violation("repeated_failure", "proposal", f"this exact action already failed {failures_for_step} "
                               f"times (retry limit {max_retries})"))
        req_hash = canonical_hash({"tool": p.tool_id, "operation": p.operation, "arguments": p.arguments})
        seq = [h["request_hash"] for h in history] + [req_hash]
        if len(seq) >= 4 and seq[-4:] == [seq[-2], seq[-1], seq[-2], seq[-1]] and seq[-1] != seq[-2]:
            v.append(Violation("circular_pattern", "proposal", "alternating between the same two actions without progress"))
        recent = [h for h in history if h.get("state") == "succeeded"][-self.window:]
        if len(recent) == self.window and len({h["request_hash"] for h in recent}) == 1 and \
                recent[-1]["request_hash"] == req_hash and len({h.get("output_hash") for h in recent}) == 1:
            v.append(Violation("no_progress", "proposal", f"the same action produced the same result {self.window} times"))
        return v, args

    @staticmethod
    def check_completion(claim: CompletionClaim, c: TaskContract, executions: list[dict],
                         evidence_rows: dict[str, dict | None]) -> list[Violation]:
        v = []
        if claim.contract_version != c.contract_version:
            v.append(Violation("contract_version_mismatch", "contract_version", "stale contract version"))
        if not any(e["state"] == "succeeded" for e in executions):
            v.append(Violation("completion_without_evidence", "claim", "no successful execution exists for this attempt"))
        for eid in claim.evidence_ids:
            row = evidence_rows.get(eid)
            if row is None:
                v.append(Violation("fabricated_evidence", "evidence_ids", f"evidence {eid} does not exist"))
            elif row["task_id"] != c.task_id or row["objective_id"] != c.parent_objective_id:
                v.append(Violation("fabricated_evidence", "evidence_ids", f"evidence {eid} belongs to another task"))
        return v
