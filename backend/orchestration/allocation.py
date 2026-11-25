"""Deterministic tool discovery, selection and per-task pool allocation (Stage 01A §6, §7).

The full catalogue is never shown to a model. The allocator filters by task type, operation, input/output
compatibility, live availability, task restrictions, resource scope, budget, permissions and model needs, and
returns at most `max_tools_per_task` tools (trusted per-task-type exceptions only). If nothing fits, it returns a
structured BLOCKED result naming what is missing. A model may only choose among the filtered tools."""
from __future__ import annotations

from dataclasses import dataclass, field

from backend.policy_engine import Catalog

from .catalogue import CatalogueTool, ToolCatalogue
from .config import ControllerConfig


@dataclass(frozen=True)
class ToolRequirement:
    task_type: str
    operations: tuple[str, ...]                 # operations the task needs, e.g. ("write", "validate")
    input_fields: tuple[str, ...] = ()          # argument names the task can supply
    output_fields: tuple[str, ...] = ()         # fields the task needs back
    resource_types: tuple[str, ...] = ()
    prohibited_operations: tuple[str, ...] = ()
    max_risk: str = "low"
    needs_cancellable: bool = False


@dataclass
class AllocationResult:
    status: str                                 # ALLOCATED | BLOCKED
    tool_ids: list[str] = field(default_factory=list)
    candidates: list[str] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)
    rejected: dict[str, str] = field(default_factory=dict)


_RISK = {"low": 0, "medium": 1, "high": 2, "critical": 3}


class ToolAllocator:
    def __init__(self, catalogue: ToolCatalogue, catalog: Catalog, config: ControllerConfig):
        self.catalogue, self.catalog, self.config = catalogue, catalog, config

    def limit_for(self, task_type: str) -> int:
        return int(self.config.tool_limit_exceptions.get(task_type, self.config.limits.max_tools_per_task))

    def candidates(self, req: ToolRequirement) -> tuple[list[CatalogueTool], dict[str, str]]:
        """Deterministic filter. Returns compatible tools and the reason each other tool was excluded."""
        keep, rejected = [], {}
        self.catalogue.refresh_availability()
        for t in self.catalogue.all():
            s = t.spec
            why = None
            if req.task_type not in s.task_types:
                why = "different task type"
            elif not set(s.operations) & set(req.operations):
                why = "no required operation"
            elif set(s.operations) <= set(req.prohibited_operations):
                why = "only prohibited operations"
            elif not t.available:
                why = f"capability {t.availability}"
            elif _RISK[s.risk.value] > _RISK[req.max_risk]:
                why = f"risk {s.risk.value} above task maximum {req.max_risk}"
            elif not s.permissions <= self.catalog.controller_granted:
                why = "requires permissions the controller is not granted"
            elif req.resource_types and s.resource_types and not set(s.resource_types) & set(req.resource_types):
                why = "unsupported resource type"
            elif req.needs_cancellable and not s.cancellable:
                why = "not cancellable"
            else:
                required_in = set(s.input_schema.get("required", []))
                if not required_in <= set(req.input_fields) and req.input_fields:
                    why = f"needs inputs the task cannot supply: {sorted(required_in - set(req.input_fields))}"
                elif req.output_fields:
                    provided = set((s.output_schema.get("properties") or {}).keys())
                    if not set(req.output_fields) & provided:
                        why = "output not compatible"
            if why:
                rejected[t.number] = why
            else:
                keep.append(t)
        return keep, rejected

    def allocate(self, req: ToolRequirement, proposed: list[str] | None = None) -> AllocationResult:
        cands, rejected = self.candidates(req)
        cand_ids = [t.number for t in cands]
        limit = self.limit_for(req.task_type)
        if proposed is not None:    # a model's proposal is validated against the filtered list, never trusted
            unknown = [p for p in proposed if p not in cand_ids]
            if unknown:
                return AllocationResult("BLOCKED", candidates=cand_ids, rejected=rejected,
                                        missing=[f"proposed tool {u} is not among the compatible tools" for u in unknown])
            chosen = list(dict.fromkeys(proposed))
        else:
            chosen = []
            for op in req.operations:   # deterministic: lowest number covering each operation, then fill
                for t in cands:
                    if op in t.spec.operations and t.number not in chosen:
                        chosen.append(t.number)
                        break
        covered = {op for n in chosen for op in self.catalogue.get(n).spec.operations}
        missing = [f"no available tool provides operation '{op}' for task type '{req.task_type}'"
                   for op in req.operations if op not in covered]
        if missing:
            return AllocationResult("BLOCKED", candidates=cand_ids, rejected=rejected, missing=missing)
        if len(chosen) > limit:
            return AllocationResult("BLOCKED", candidates=cand_ids, rejected=rejected,
                                    missing=[f"task needs {len(chosen)} tools; limit is {limit} — decompose the task "
                                             "or configure a trusted exception"])
        return AllocationResult("ALLOCATED", tool_ids=sorted(chosen), candidates=cand_ids, rejected=rejected)
