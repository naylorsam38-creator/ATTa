"""Independent verification of an installed integration (Stage 12 §44).

The integration cannot verify itself: criteria come from the APPROVED manifest snapshot in the append-only
database table (written before any integration code could run), and every observation is taken from the ATTa side —
registry state, generated package bytes, catalogue numbers, policy catalog. No integration code is called here
(the registry's isolated probe/health are Stage 01A runtime actions with ATTa-signed tickets)."""
from __future__ import annotations

from dataclasses import dataclass, field

from backend.capability_registry.package import package_digest
from backend.contracts import AvailabilityState, canonical_hash

from .packaging import tool_output_schema
from .types import RISK_ORDER


@dataclass
class VerificationReport:
    passed: bool
    checks: dict = field(default_factory=dict)
    problems: list[str] = field(default_factory=list)
    deferred: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {"passed": self.passed, "checks": self.checks, "problems": self.problems, "deferred": self.deferred,
                "verifier": "atta-integration-verifier/1"}


class _M:   # minimal manifest view for tool_output_schema
    def __init__(self, raw):
        from .types import IntegrationType
        self.raw, self.type = raw, IntegrationType(raw["integration_type"])


class IntegrationVerifier:
    NAME = "atta-integration-verifier/1"

    def __init__(self, registry, catalogue, catalog, store):
        self.registry, self.catalogue, self.catalog, self.store = registry, catalogue, catalog, store

    def verify(self, rec, *, package_dir=None, expected_block: bool = False) -> VerificationReport:
        """expected_block: the trust class / isolation state means the registry is EXPECTED to refuse execution
        (UNTRUSTED, or RESTRICTED without an OS sandbox): structure is verified, the isolated probe is deferred."""
        p, checks, deferred = [], {}, []
        snap = self.store.manifest(rec.manifest_digest)
        approved = snap["manifest"] if snap else None
        checks["manifest_snapshot"] = approved is not None and canonical_hash(approved) == rec.manifest_digest
        if not checks["manifest_snapshot"]:
            p.append("approved manifest snapshot missing or altered")
            return VerificationReport(False, checks, p)
        if not approved.get("tools"):
            return VerificationReport(True, checks, p)
        reg = self.registry.get(rec.capability_id)
        checks["registered"] = reg is not None and reg.manifest is not None
        if not checks["registered"]:
            p.append(f"capability {rec.capability_id} is not registered ({reg.state.value if reg else 'absent'}: "
                     f"{reg.problems if reg else ''})")
            return VerificationReport(False, checks, p)
        m = reg.manifest
        checks["identity"] = m.id == rec.capability_id and m.version == approved["version"]
        if package_dir is not None:
            checks["package_digest"] = package_digest(package_dir) == rec.package_digest == reg.package_digest
        else:
            checks["package_digest"] = reg.package_digest == rec.package_digest
        checks["permissions"] = set(m.permissions) == set(approved["permissions_requested"])
        max_risk = max((t["risk"] for t in approved["tools"]), key=lambda r: RISK_ORDER[r])
        checks["risk"] = m.risk.value == max_risk
        unknown = sorted(set(approved["permissions_requested"]) - set(self.catalog.permissions))
        checks["permissions_in_catalog"] = not unknown
        if unknown:
            p.append(f"permissions not in the operator catalog: {unknown}")
        tools = {t.name: t for t in reg.tools}
        tool_ok = {}
        mm = _M(approved)
        for t in approved["tools"]:
            s = tools.get(t["name"])
            problems = []
            if s is None:
                problems.append("not registered")
            else:
                if canonical_hash(s.input_schema) != canonical_hash(t["input_schema"]):
                    problems.append("input schema differs")
                if canonical_hash(s.output_schema) != canonical_hash(tool_output_schema(mm, t)):
                    problems.append("output schema differs")
                if set(s.permissions) != set(t["permissions"]):
                    problems.append("permissions differ")
                if s.risk.value != t["risk"]:
                    problems.append("risk differs")
                if not s.verify.startswith("_atta_tools_verify:"):
                    problems.append("verify handler is not the independent ATTa verifier")
                number = rec.numbers.get(t["name"])
                cat = self.catalogue.get(number) if number else None
                if cat is None or cat.spec.string_id != f"{rec.capability_id}:{t['name']}":
                    problems.append(f"catalogue number {number} does not identify this tool")
            tool_ok[t["name"]] = problems or "ok"
            p += [f"tool {t['name']}: {x}" for x in problems]
        extra = sorted(set(tools) - {t["name"] for t in approved["tools"]})
        if extra:
            p.append(f"registered tools not in the approved manifest: {extra}")
        checks["tools"] = tool_ok
        if reg.state is AvailabilityState.AVAILABLE:
            checks["probe_and_health"] = "passed"
        elif reg.state in (AvailabilityState.DEPENDENCY_MISSING, AvailabilityState.DISABLED) or (
                reg.state is AvailabilityState.UNTRUSTED and expected_block):
            deferred.append(f"isolated probe/health deferred: {reg.state.value} ({'; '.join(reg.problems)[:300]})")
            checks["probe_and_health"] = "deferred"
        else:
            p.append(f"registry state {reg.state.value}: {'; '.join(reg.problems)[:300]}")
            checks["probe_and_health"] = "failed"
        for k in ("identity", "package_digest", "permissions", "risk"):
            if not checks[k]:
                p.append(f"{k} check failed")
        checks["policy_preview"] = {
            t["name"]: ("requires Stage 02 human approval" if t["risk"] in self.catalog.approval_required_risks else
                        "allocatable by the controller" if set(t["permissions"]) <= self.catalog.controller_granted
                        else "permissions not granted to the controller (operator decision)")
            for t in approved["tools"]}
        return VerificationReport(not p, checks, p, deferred)
