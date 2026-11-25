"""The single capability registry (Stage 01 + Stage 01A corrections 2, 3 and 4).

discover():
  1. parses every <dir>/*/capability.yaml — schema validation only, NO capability code is imported here;
  2. rejects every copy of a duplicated id;
  3. walks capabilities in TOPOLOGICAL order, so a dependency's final state (including its health) is known
     before its dependents are evaluated and failures propagate to every dependent;
  4. per capability: disabled? -> capability/service/python dependencies -> operator trust + package digest ->
     isolated probe (imports + handler signatures, in a separate process) -> isolated health check.
Service availability is read from a live callable (the HealthMonitor), never a startup snapshot, and
refresh_dependencies() re-evaluates states whenever service health changes. revalidate() repeats the integrity,
trust, dependency and service checks immediately before any execution.

Execution is NOT offered here: tools run only through the runtime controller's execution gateway."""
from __future__ import annotations

import importlib.util
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable

from backend.capability_runtime import CapabilityRuntime
from backend.contracts import (ApiError, AvailabilityState, CapabilityAvailability, ErrorCode, get_logger, iso_utc,
                               new_id)
from backend.policy_engine import Catalog, PolicyEngine, get_policy_engine

from .manifest import MANIFEST_FILENAME, CapabilityManifest, ManifestError, ToolSpec, load_manifest_file, tools_of
from .package import PackageError, package_digest
from .trust import TrustStore

log = get_logger("atta.capabilities")


class CapabilityNotExecutable(ApiError):
    def __init__(self, cap_id: str, reason: str):
        super().__init__(ErrorCode.CAPABILITY_UNAVAILABLE, f"capability '{cap_id}' is not executable: {reason}")


@dataclass
class RegisteredCapability:
    id: str
    source: str
    state: AvailabilityState
    manifest: CapabilityManifest | None = None
    problems: list[str] = field(default_factory=list)
    health_detail: object = None
    checked_at: str | None = None
    package_digest: str | None = None
    tools: list[ToolSpec] = field(default_factory=list)
    probe_problems: list[str] | None = None      # cached isolated probe result for this digest
    health_ok: bool | None = None
    health_at: float = 0.0
    fixed: bool = False      # invalid for structural reasons (parse/tools/package/cycle): never re-evaluated

    @property
    def executable(self) -> bool:
        return self.state is AvailabilityState.AVAILABLE and self.manifest is not None

    def availability(self) -> CapabilityAvailability:
        m = self.manifest
        return CapabilityAvailability(id=self.id, version=m.version if m else None, state=self.state,
                                      executable=self.executable, source=self.source,
                                      requirement=m.requirement if m else None, risk=m.risk if m else None,
                                      problems=list(self.problems))


def _default_trust() -> TrustStore:
    from backend.settings import load_settings
    from backend.settings.loader import resolve_path
    try:
        s = load_settings()
    except Exception as exc:  # noqa: BLE001
        return TrustStore.empty(f"settings unavailable: {exc}")
    return TrustStore.load(resolve_path(s.capabilities.trust_store), resolve_path(s.capabilities.trust_public_key))


class CapabilityRegistry:
    def __init__(self, directories: Iterable[Path], *, policy: PolicyEngine | None = None,
                 service_available: Callable[[str], bool] | None = None, healthcheck_timeout_s: float = 5.0,
                 max_timeout_s: float = 3600.0, trust: TrustStore | None = None,
                 runtime: CapabilityRuntime | None = None, catalog: Catalog | None = None,
                 health_max_age_s: float = 30.0):
        self.directories = [Path(d) for d in directories]
        self.policy = policy or get_policy_engine()
        self.catalog = catalog or self.policy.catalog
        self.service_available = service_available or (lambda name: False)
        self.healthcheck_timeout_s = healthcheck_timeout_s
        self.max_timeout_s = max_timeout_s
        self.trust = trust if trust is not None else _default_trust()
        self.runtime = runtime or CapabilityRuntime()
        self.health_max_age_s = health_max_age_s
        self._caps: dict[str, RegisteredCapability] = {}
        self._rejected: list[RegisteredCapability] = []
        self._order: list[str] = []
        self._lock = threading.RLock()
        self.discovered_at: str | None = None

    # ---------------------------------------------------------------- discovery
    def manifest_paths(self) -> list[Path]:
        paths: list[Path] = []
        for d in self.directories:
            if d.is_dir():
                paths.extend(sorted(p for p in d.glob(f"*/{MANIFEST_FILENAME}") if p.is_file()))
        return paths

    def discover(self) -> list[CapabilityAvailability]:
        caps: dict[str, RegisteredCapability] = {}
        rejected: list[RegisteredCapability] = []
        loaded: list[CapabilityManifest] = []
        for path in self.manifest_paths():
            try:
                loaded.append(load_manifest_file(path, self.max_timeout_s))
            except ManifestError as exc:
                rejected.append(RegisteredCapability(id=self._guess_id(path), source=str(path),
                                                     state=AvailabilityState.INVALID, problems=exc.problems))
        by_id: dict[str, list[CapabilityManifest]] = {}
        for m in loaded:
            by_id.setdefault(m.id, []).append(m)
        for cid, ms in by_id.items():
            if len(ms) > 1:   # ambiguous identity: none of them may execute
                srcs = ", ".join(str(m.source) for m in ms)
                rejected += [RegisteredCapability(id=cid, source=str(m.source), state=AvailabilityState.REJECTED_DUPLICATE,
                                                  problems=[f"duplicate capability id '{cid}' in: {srcs}"]) for m in ms]
                continue
            m = ms[0]
            rc = RegisteredCapability(id=cid, source=str(m.source), state=AvailabilityState.AVAILABLE, manifest=m)
            try:
                rc.tools = tools_of(m)
                rc.package_digest = package_digest(m.directory)
            except (ManifestError, PackageError) as exc:
                rc.state, rc.fixed = AvailabilityState.INVALID, True
                rc.problems = getattr(exc, "problems", [str(exc)])
            caps[cid] = rc
        order = self._topological(caps)
        with self._lock:
            self._caps, self._rejected, self._order = caps, rejected, order
        self._evaluate(run_isolated=True)
        self.discovered_at = iso_utc()
        report = self.availability()
        for a in report:
            log.info("capability %s: %s", a.id, a.state.value, extra={"capability": a.id, "state": a.state.value,
                                                                      "problems": a.problems})
        return report

    @staticmethod
    def _guess_id(path: Path) -> str:
        try:
            import yaml
            data = yaml.safe_load(path.read_text(encoding="utf-8"))
            if isinstance(data, dict) and isinstance(data.get("id"), str):
                return data["id"]
        except Exception:  # noqa: BLE001
            pass
        return f"<invalid:{path.parent.name}>"

    @staticmethod
    def _topological(caps: dict[str, RegisteredCapability]) -> list[str]:
        indeg = {cid: 0 for cid in caps}
        users: dict[str, list[str]] = {cid: [] for cid in caps}
        for cid, c in caps.items():
            if c.manifest:
                for dep in c.manifest.capability_dependencies:
                    if dep in caps:
                        indeg[cid] += 1
                        users[dep].append(cid)
        ready, order = sorted(cid for cid, n in indeg.items() if n == 0), []
        while ready:
            cid = ready.pop(0)
            order.append(cid)
            for u in sorted(users[cid]):
                indeg[u] -= 1
                if indeg[u] == 0:
                    ready.append(u)
        for cid in caps:
            if cid not in order:
                caps[cid].state, caps[cid].fixed = AvailabilityState.INVALID, True
                caps[cid].problems = ["capability dependency cycle"]
        return order

    def _evaluate(self, run_isolated: bool) -> None:
        """One topological pass. Dependencies are final before dependents, so failures propagate transitively."""
        with self._lock:
            caps, order = self._caps, list(self._order)
        for cid in order:
            c = caps[cid]
            if c.manifest is None or c.fixed:
                continue
            m = c.manifest
            state, problems = AvailabilityState.AVAILABLE, []
            if not m.enabled:
                state, problems = AvailabilityState.DISABLED, ["disabled in manifest"]
            else:
                missing = []
                for dep in m.capability_dependencies:
                    if dep not in caps:
                        missing.append(f"capability '{dep}' is not installed")
                    elif not caps[dep].executable:
                        missing.append(f"capability '{dep}' is {caps[dep].state.value}")
                for svc in m.service_dependencies:
                    try:
                        ok = bool(self.service_available(svc))
                    except Exception:  # noqa: BLE001
                        ok = False
                    if not ok:
                        missing.append(f"service '{svc}' is unavailable")
                for mod in m.python_dependencies:
                    try:   # top-level lookup only: find_spec of a top-level name runs no module code
                        found = importlib.util.find_spec(mod.split(".")[0]) is not None
                    except (ImportError, ValueError):
                        found = False
                    if not found:
                        missing.append(f"python module '{mod}' is not installed")
                unknown_perms = sorted(p for t in c.tools for p in t.permissions if p not in self.catalog.permissions)
                if missing:
                    state, problems = AvailabilityState.DEPENDENCY_MISSING, missing
                else:
                    t_state, t_problems = self.trust.check(m, c.package_digest or "")
                    if t_state is not None:
                        state, problems = AvailabilityState(t_state), t_problems
                    elif unknown_perms:
                        state, problems = AvailabilityState.INVALID, [f"permissions not in the catalog: {unknown_perms}"]
                    else:
                        if c.probe_problems is None and run_isolated:
                            c.probe_problems = self._isolated(c, "probe").get("problems", ["probe failed"])
                        if c.probe_problems:
                            state, problems = AvailabilityState.INVALID, list(c.probe_problems)
                        else:
                            stale = time.time() - c.health_at > self.health_max_age_s
                            if run_isolated or c.health_ok is None or stale:
                                self._healthcheck(c)
                            if not c.health_ok:
                                state = AvailabilityState.UNHEALTHY
                                problems = [f"healthcheck reported unhealthy: {c.health_detail!r}"]
            if state is not c.state:
                log.info("capability %s %s -> %s", cid, c.state.value, state.value, extra={"problems": problems})
            c.state, c.problems = state, problems

    def _isolated(self, c: RegisteredCapability, mode: str) -> dict:
        ticket = self.runtime.issue_ticket(new_id("exe"), c.id, mode)
        tool_handlers = [[t.name, kind, ref] for t in c.tools for kind, ref in (("execute", t.handler), ("verify", t.verify))]
        r = self.runtime.run(ticket=ticket, mode=mode, capability_id=c.id, package_dir=c.manifest.directory,
                             expected_digest=c.package_digest or "", handlers=c.manifest.handler_refs,
                             timeout_s=self.healthcheck_timeout_s, tool_handlers=tool_handlers)
        if r.status == "ok":
            return r.value if isinstance(r.value, dict) else {"value": r.value}
        return {"problems": [f"isolated {mode} {r.status}: {r.error or ''}".strip()], "status": r.status}

    def _healthcheck(self, c: RegisteredCapability) -> None:
        c.checked_at, c.health_at = iso_utc(), time.time()
        ticket = self.runtime.issue_ticket(new_id("exe"), c.id, "healthcheck")
        r = self.runtime.run(ticket=ticket, mode="healthcheck", capability_id=c.id, package_dir=c.manifest.directory,
                             expected_digest=c.package_digest or "", handlers=c.manifest.handler_refs,
                             timeout_s=self.healthcheck_timeout_s)
        if r.status != "ok":
            c.health_ok, c.health_detail = False, f"{r.status}: {r.error or ''}".strip()
            return
        v = r.value
        c.health_ok = (v.get("healthy") is True) if isinstance(v, dict) else v is True
        c.health_detail = v

    def refresh_dependencies(self) -> list[CapabilityAvailability]:
        """Re-evaluate states without re-probing (called when service health changes)."""
        self._evaluate(run_isolated=False)
        return self.availability()

    def recheck_health(self) -> list[CapabilityAvailability]:
        with self._lock:
            for c in self._caps.values():
                c.health_at = 0.0
        self._evaluate(run_isolated=False)
        return self.availability()

    # ---------------------------------------------------------------- queries
    def availability(self) -> list[CapabilityAvailability]:
        with self._lock:
            items = [c.availability() for c in self._caps.values()] + [r.availability() for r in self._rejected]
        return sorted(items, key=lambda a: (a.id, a.source))

    def get(self, cap_id: str) -> RegisteredCapability | None:
        with self._lock:
            return self._caps.get(cap_id)

    def executable_ids(self) -> list[str]:
        with self._lock:
            return sorted(cid for cid, c in self._caps.items() if c.executable)

    def all_tools(self) -> list[ToolSpec]:
        with self._lock:
            return [t for c in self._caps.values() for t in c.tools]

    def tool(self, string_id: str) -> tuple[RegisteredCapability, ToolSpec] | None:
        cid = string_id.split(":")[0]
        c = self.get(cid)
        if c is None:
            return None
        for t in c.tools:
            if t.string_id == string_id:
                return c, t
        return None

    def revalidate(self, cap_id: str) -> RegisteredCapability:
        """Immediately before execution: package bytes, trust, dependencies and live service health again."""
        c = self.get(cap_id)
        if c is None:
            raise ApiError(ErrorCode.NOT_FOUND, f"capability '{cap_id}' is not registered")
        try:
            now_digest = package_digest(c.manifest.directory)
        except (PackageError, OSError) as exc:
            c.state, c.problems = AvailabilityState.INTEGRITY_FAILED, [str(exc)]
            raise CapabilityNotExecutable(cap_id, str(exc)) from exc
        if now_digest != c.package_digest:
            c.state, c.problems = AvailabilityState.INTEGRITY_FAILED, ["package changed on disk since discovery"]
            raise CapabilityNotExecutable(cap_id, "package changed on disk since discovery")
        self._evaluate(run_isolated=False)
        if not c.executable:
            raise CapabilityNotExecutable(cap_id, f"{c.state.value}: {'; '.join(c.problems)}")
        return c

    def execute(self, *args, **kwargs):
        """Removed in Stage 01A: direct execution would bypass task contracts, drift checks and budgets."""
        raise CapabilityNotExecutable(str(args[0]) if args else "?", "direct execution is disabled; tools run only "
                                      "through the runtime controller's execution gateway")
