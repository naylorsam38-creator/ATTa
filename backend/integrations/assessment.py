"""Live runtime assessment of an installed integration — ONE function used by the registry trust gate (before
every capability evaluation, i.e. before every execution), by the execution guard and by health. Nothing here is a
startup snapshot: state, trust, connectivity, isolation and credential availability are read when asked."""
from __future__ import annotations

from dataclasses import dataclass, field

from .credentials import CredentialBroker, credential_ref
from .isolation import IsolationProvider
from .network import NetworkPolicy
from .types import (EXECUTABLE_TRUST, ExecutionMode, HealthStatus, IntegrationState, SideEffect, TrustClass)


@dataclass
class Installed:
    """In-memory view of one installed integration version, rebuilt from the database on start."""
    integration_id: str
    integration_type: str
    version: str
    state: IntegrationState
    trust_class: TrustClass
    manifest: dict                      # the APPROVED manifest snapshot (from integrations.integration_manifests)
    manifest_digest: str
    capability_id: str | None
    package_digest: str | None          # digest of the generated capability package (what the registry verifies)
    binding: str
    numbers: dict[str, str] = field(default_factory=dict)   # tool name -> permanent number

    @property
    def tools(self) -> list[dict]:
        return list(self.manifest.get("tools") or [])

    @property
    def max_risk(self) -> str:
        from .types import RISK_ORDER
        return max([t["risk"] for t in self.tools] or ["low"], key=lambda r: RISK_ORDER[r])

    def tool_by_number(self, number: str) -> dict | None:
        for n, num in self.numbers.items():
            if num == number:
                return next((t for t in self.tools if t["name"] == n), None)
        return None


@dataclass
class Assessment:
    blocked: list[str] = field(default_factory=list)       # BLOCKED (trust, isolation, integrity)
    stopped: list[str] = field(default_factory=list)       # not enabled / being removed
    offline: list[str] = field(default_factory=list)       # OFFLINE_UNAVAILABLE
    auth: list[str] = field(default_factory=list)          # AUTH_REQUIRED
    dependencies: list[str] = field(default_factory=list)  # DEGRADED: dependency integrations unavailable

    @property
    def executable(self) -> bool:
        return not (self.blocked or self.stopped or self.offline or self.auth or self.dependencies)

    def status(self) -> HealthStatus | None:
        if self.blocked:
            return HealthStatus.BLOCKED
        if self.stopped:
            return HealthStatus.STOPPED
        if self.offline:
            return HealthStatus.OFFLINE_UNAVAILABLE
        if self.auth:
            return HealthStatus.AUTH_REQUIRED
        if self.dependencies:
            return HealthStatus.DEGRADED
        return None

    def reasons(self) -> list[str]:
        return self.blocked + self.stopped + self.offline + self.auth + self.dependencies


def needs_isolation(rec: Installed) -> bool:
    return rec.trust_class is TrustClass.RESTRICTED or \
        rec.manifest.get("execution_mode") == ExecutionMode.ISOLATED_SANDBOX.value


def assess(rec: Installed, *, network: NetworkPolicy, isolation: IsolationProvider, broker: CredentialBroker,
           lookup, allow_states: frozenset[IntegrationState] = frozenset({IntegrationState.ENABLED})) -> Assessment:
    a = Assessment()
    if rec.state not in allow_states:
        a.stopped.append(f"integration state is {rec.state.value}")
    if rec.trust_class not in EXECUTABLE_TRUST:
        a.blocked.append(f"trust class {rec.trust_class.value} never executes")
    if needs_isolation(rec):
        rep = isolation.report()
        if not rep.available:
            a.blocked.append(f"OS isolation required: {rep.reason}")
    if rec.trust_class is TrustClass.RESTRICTED:
        writes = [t["name"] for t in rec.tools if t["side_effect"] != SideEffect.READ_ONLY.value]
        if writes:
            a.blocked.append(f"RESTRICTED integrations may only expose READ_ONLY tools (not {writes})")
    hosts = tuple(rec.manifest.get("network_requirements", {}).get("hosts") or [])
    a.offline += network.integration_offline(hosts)
    for c in rec.manifest.get("credential_requirements") or []:
        if c["required"]:
            st = broker.status(credential_ref(rec.integration_id, c["name"]))
            if not st.available:
                a.auth.append(f"credential '{c['name']}' unavailable: {st.reason}")
    for dep in (rec.manifest.get("dependencies") or {}).get("integrations") or []:
        other = lookup(dep)
        if other is None or other.state is not IntegrationState.ENABLED:
            a.dependencies.append(f"dependency integration '{dep}' is not enabled")
    return a
