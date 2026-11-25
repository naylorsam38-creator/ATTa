"""Shared helpers for the S01A acceptance tests."""
from __future__ import annotations

import json
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from s01a_harness import ROOT, ExplicitTestProposals, Harness, TaskSpec, build  # noqa: E402,F401

from backend.orchestration.contracts import ToolProposal  # noqa: E402

EVIDENCE = ROOT / "evidence" / "stage_01a" / "acceptance"


def record(test_id: str, data) -> None:
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    (EVIDENCE / f"{test_id}.json").write_text(json.dumps(data, indent=2, default=str))


def P(task_id: str, tool: str, op: str, args: dict, targets=(), reqs=("FR-01",), pid: str | None = None,
      version: int = 1) -> ToolProposal:
    return ToolProposal(proposal_id=pid or f"p-{uuid.uuid4().hex[:10]}", task_id=task_id, contract_version=version,
                        tool_id=tool, operation=op, arguments=args, target_resources=list(targets),
                        expected_result="test", referenced_requirement_ids=list(reqs))


def start(h: Harness, objective_id: str, spec: TaskSpec, plan: bool = True):
    """Plan (optionally), release and move one task to RUNNING; returns (contract, worker context)."""
    if plan:
        h.controller.plan(objective_id, [spec])
    released = h.controller.release(objective_id)
    assert spec.task_id in released, released
    t = h.store.transition(objective_id, spec.task_id, "RUNNING", "test start", h.controller.controller_id,
                           expect=["QUEUED"])
    c = h.store.contract(objective_id, spec.task_id)
    return c, h.controller.gateway.issue_context(objective_id, spec.task_id, t["attempt_id"], c.contract_hash,
                                                 c.assigned_worker)


def rules(result) -> list[str]:
    return [v.rule for v in result.violations]
