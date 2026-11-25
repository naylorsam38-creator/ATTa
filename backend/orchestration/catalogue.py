"""Numbered tool catalogue (Stage 01A §5): registry tools + permanent numbers, reconciled with the database."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from backend.capability_registry import CapabilityRegistry, ToolSpec
from backend.capability_registry.numbering import NumberingError, capability_number, load_numbers, reconcile_with_store
from backend.database.session import Database


@dataclass(frozen=True)
class CatalogueTool:
    number: str                 # permanent numeric identifier, e.g. "040.001"
    spec: ToolSpec              # string id, capability id, schemas, handler refs, permissions, risk, limits ...
    capability_number: str
    available: bool
    availability: str

    def describe(self) -> dict:
        s = self.spec
        return {"tool_id": self.number, "string_id": s.string_id, "capability_id": s.capability_id,
                "capability_number": self.capability_number, "name": s.name, "description": s.description,
                "operations": list(s.operations), "input_schema": s.input_schema, "output_schema": s.output_schema,
                "required_permissions": sorted(s.permissions), "risk": s.risk.value,
                "resource_types": list(s.resource_types), "timeout_s": s.timeout_s,
                "resource_limits": s.resource_limits, "cancellable": s.cancellable, "idempotency": s.idempotency,
                "version": s.version, "available": self.available, "availability": self.availability}


class ToolCatalogue:
    def __init__(self, registry: CapabilityRegistry, numbers_file: Path, db: Database, namespace: str = "default"):
        self.registry, self.numbers_file, self.db, self.namespace = registry, numbers_file, db, namespace
        self.problems: list[str] = []
        self._by_number: dict[str, CatalogueTool] = {}
        self._unnumbered: list[str] = []

    def load(self) -> "ToolCatalogue":
        self.problems, self._by_number, self._unnumbered = [], {}, []
        try:
            entries = load_numbers(self.numbers_file)
        except NumberingError as exc:
            self.problems = [str(exc)]
            return self
        with self.db.tx() as x:
            problems = reconcile_with_store(entries, x, self.namespace)
        if problems:
            self.problems = problems
            return self       # an inconsistent numbering file makes NO tool allocatable
        by_tool = {e.tool: e for e in entries.values() if not e.retired}
        for spec in self.registry.all_tools():
            e = by_tool.get(spec.string_id)
            reg = self.registry.get(spec.capability_id)
            capno = capability_number(reg.manifest.requirement if reg and reg.manifest else None)
            if e is None:
                self._unnumbered.append(spec.string_id)
                continue
            if capno is None or not e.number.startswith(capno + "."):
                self.problems.append(f"{spec.string_id}: number {e.number} does not match its capability "
                                     f"{reg.manifest.requirement if reg and reg.manifest else '?'}")
                continue
            self._by_number[e.number] = CatalogueTool(e.number, spec, capno, bool(reg and reg.executable),
                                                      reg.state.value if reg else "missing")
        return self

    def refresh_availability(self) -> None:
        for n, t in list(self._by_number.items()):
            reg = self.registry.get(t.spec.capability_id)
            self._by_number[n] = CatalogueTool(n, t.spec, t.capability_number, bool(reg and reg.executable),
                                               reg.state.value if reg else "missing")

    def get(self, number: str) -> CatalogueTool | None:
        return self._by_number.get(number)

    def all(self) -> list[CatalogueTool]:
        return [self._by_number[k] for k in sorted(self._by_number)]

    @property
    def unnumbered(self) -> list[str]:
        return list(self._unnumbered)
