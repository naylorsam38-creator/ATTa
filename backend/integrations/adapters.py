"""Adapters to the other parallel stages' REAL interfaces (Stage 12 merge preparation).

Stage 01B (Worker A — adaptive deployment, isolation, offline qualification), as published on
`claude/ai-app-stage-01-foundation-82jy3a` at 0ec939c:
  * `CapabilityRuntime(network=, worker_user=, isolation_required=, windows_isolation=)` — the child applies a private
    network namespace / unprivileged account / cgroups / Windows restricted token BEFORE loading capability code and
    refuses to run when isolation is required but unavailable;
  * `settings.deployment.profile` (`AIR_GAPPED` …) and `configuration/profiles/*.yaml` (`worker_network: none`);
  * the gateway gives a worker host networking only when the task contract permits network destinations.
Stage 11 (Worker 3 — research), on `claude/focused-cori-ajjjn0` at bb1a60b:
  * `NetworkModeProvider.current_mode() -> NetworkMode(ONLINE|OFFLINE|AIR_GAPPED)`, env `ATTA_NETWORK_MODE`.

These adapters only READ those interfaces (duck-typed, nothing imported from the other stages), so the sidecar works
on the Stage 01A baseline, on 01B, on 11, and on any merge of them. When the other stage is absent the adapter
reports so honestly (isolation UNAVAILABLE) instead of pretending."""
from __future__ import annotations

import os
from dataclasses import dataclass

from .isolation import SUPERVISED_GUARANTEES, IsolationReport
from .network import NetworkPolicy
from .types import CONNECTIVITY_ORDER, ConnectivityMode


def _mode(value) -> ConnectivityMode:
    v = getattr(value, "value", value)
    try:
        return ConnectivityMode(str(v).strip().upper())
    except ValueError:
        return ConnectivityMode.AIR_GAPPED       # unknown values fail closed (same rule as Stage 11)


class ConnectivitySources:
    """Collects every connectivity signal and returns the MOST RESTRICTIVE one, live, on each call:
      1. an injected provider (Stage 01B qualified mode or a Stage 11 NetworkModeProvider: `current_mode()`);
      2. `ATTA_NETWORK_MODE` (the variable Stage 11 already honours);
      3. Stage 01B `settings.deployment.profile == "AIR_GAPPED"`;
      4. `configuration/integrations.yaml` `connectivity`."""

    def __init__(self, configured: ConnectivityMode, settings=None, provider=None, environ=None):
        self.configured, self.settings, self.provider = configured, settings, provider
        self.environ = os.environ if environ is None else environ

    def signals(self) -> dict[str, ConnectivityMode]:
        out = {"integrations.yaml": self.configured}
        if self.provider is not None:
            out["provider"] = _mode(self.provider.current_mode())
        env = (self.environ.get("ATTA_NETWORK_MODE") or "").strip()
        if env:
            out["ATTA_NETWORK_MODE"] = _mode(env)
        dep = getattr(self.settings, "deployment", None)
        profile = str(getattr(dep, "profile", "") or "").upper()
        if profile == "AIR_GAPPED":
            out["stage01b.deployment.profile"] = ConnectivityMode.AIR_GAPPED
        return out

    def current_mode(self) -> ConnectivityMode:
        return max(self.signals().values(), key=lambda m: CONNECTIVITY_ORDER[m.value])

    def policy(self, allowed_hosts) -> NetworkPolicy:
        return NetworkPolicy(self.configured, frozenset(allowed_hosts), provider=self.current_mode)


@dataclass
class RuntimeIsolation:
    """IsolationProvider backed by the Stage 01B CapabilityRuntime. Available only when the runtime REQUIRES
    isolation (the 01B child then refuses to run any handler whose isolation could not be applied)."""
    runtime: object

    def report(self) -> IsolationReport:
        rt = self.runtime
        if not hasattr(rt, "isolation_required"):
            return IsolationReport(False, "stage01a-supervised-runtime", SUPERVISED_GUARANTEES,
                                   "BLOCKED: the Stage 01B isolating runtime is not present in this build")
        if not getattr(rt, "isolation_required", False):
            return IsolationReport(False, "stage01b-runtime", SUPERVISED_GUARANTEES,
                                   "BLOCKED: Stage 01B isolation is present but not required by the active profile "
                                   "(deployment.worker_network != none)")
        g = list(SUPERVISED_GUARANTEES) + ["isolation REQUIRED: handler refused if not applied"]
        if getattr(rt, "network", "host") == "none":
            g.append("private empty network namespace unless the task contract permits destinations")
        if getattr(rt, "worker_user", None):
            g.append(f"unprivileged worker account {rt.worker_user}")
        if os.name == "nt" and getattr(rt, "windows_isolation", False):
            g.append("Windows restricted token, low integrity, workspace ACLs, Job Object")
        return IsolationReport(True, "stage01b-runtime", tuple(g), "Stage 01B runtime isolation required")
