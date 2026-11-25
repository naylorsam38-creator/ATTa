"""The single capability registry.

discover() scans every configured capability directory for <dir>/<name>/capability.yaml, validates each manifest,
rejects duplicate identifiers, resolves dependencies (capabilities, services, Python modules) in topological order,
runs health checks with a timeout and records availability. Only capabilities in state AVAILABLE are executable,
and every execution is evaluated by the central policy engine first.
"""
from __future__ import annotations

import importlib.util
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable

from jsonschema import Draft202012Validator

from backend.contracts import (AvailabilityState, CapabilityAvailability, ErrorCode, ApiError, RequestContext,
                               get_logger, iso_utc)
from backend.policy_engine import PolicyEngine, PolicyRequest, get_policy_engine

from .manifest import MANIFEST_FILENAME, CapabilityManifest, ManifestError, load_manifest_file

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
    health_detail: Any = None
    checked_at: str | None = None

    @property
    def executable(self) -> bool:
        return self.state is AvailabilityState.AVAILABLE and self.manifest is not None

    def availability(self) -> CapabilityAvailability:
        m = self.manifest
        return CapabilityAvailability(id=self.id, version=m.version if m else None, state=self.state,
                                      executable=self.executable, source=self.source,
                                      requirement=m.requirement if m else None, risk=m.risk if m else None,
                                      problems=list(self.problems))


@dataclass
class ExecutionResult:
    capability_id: str
    output: dict
    verified: bool
    verification_detail: Any
    duration_ms: float


def run_with_timeout(fn: Callable, args: tuple, timeout_s: float) -> Any:
    """Run fn(*args) in a daemon thread; raise TimeoutError if it does not finish in time."""
    box: dict[str, Any] = {}

    def target():
        try:
            box["value"] = fn(*args)
        except BaseException as exc:  # noqa: BLE001
            box["error"] = exc

    t = threading.Thread(target=target, daemon=True, name="capability-call")
    t.start()
    t.join(timeout_s)
    if t.is_alive():
        raise TimeoutError(f"exceeded {timeout_s}s")
    if "error" in box:
        raise box["error"]
    return box.get("value")


class CapabilityRegistry:
    def __init__(self, directories: Iterable[Path], *, policy: PolicyEngine | None = None,
                 service_available: Callable[[str], bool] | None = None, healthcheck_timeout_s: float = 5.0,
                 max_timeout_s: float = 3600.0):
        self.directories = [Path(d) for d in directories]
        self.policy = policy or get_policy_engine()
        self.service_available = service_available or (lambda name: False)
        self.healthcheck_timeout_s = healthcheck_timeout_s
        self.max_timeout_s = max_timeout_s
        self._caps: dict[str, RegisteredCapability] = {}
        self._rejected: list[RegisteredCapability] = []
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
            if len(ms) > 1:  # ambiguous identity: none of them may execute
                srcs = ", ".join(str(m.source) for m in ms)
                for m in ms:
                    rejected.append(RegisteredCapability(id=cid, source=str(m.source), manifest=None,
                                                         state=AvailabilityState.REJECTED_DUPLICATE,
                                                         problems=[f"duplicate capability id '{cid}' in: {srcs}"]))
            else:
                m = ms[0]
                caps[cid] = RegisteredCapability(id=cid, source=str(m.source), manifest=m,
                                                 state=AvailabilityState.AVAILABLE if m.enabled
                                                 else AvailabilityState.DISABLED,
                                                 problems=[] if m.enabled else ["disabled in manifest"])
        self._resolve_dependencies(caps)
        for c in caps.values():
            if c.state is AvailabilityState.AVAILABLE:
                self._healthcheck(c)
        with self._lock:
            self._caps, self._rejected = caps, rejected
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

    def _resolve_dependencies(self, caps: dict[str, RegisteredCapability]) -> None:
        # Topological pass (Kahn). Cycles and missing/unavailable dependencies make a capability unavailable.
        order: list[str] = []
        indeg = {cid: 0 for cid in caps}
        users: dict[str, list[str]] = {cid: [] for cid in caps}
        for cid, c in caps.items():
            for dep in c.manifest.capability_dependencies:
                if dep in caps:
                    indeg[cid] += 1
                    users[dep].append(cid)
        ready = sorted(cid for cid, n in indeg.items() if n == 0)
        while ready:
            cid = ready.pop(0)
            order.append(cid)
            for u in users[cid]:
                indeg[u] -= 1
                if indeg[u] == 0:
                    ready.append(u)
        for cid in caps:
            if cid not in order:
                caps[cid].state = AvailabilityState.INVALID
                caps[cid].problems.append("capability dependency cycle")
        for cid in order:
            c = caps[cid]
            if c.state is not AvailabilityState.AVAILABLE:
                continue
            m = c.manifest
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
                try:
                    found = importlib.util.find_spec(mod) is not None
                except (ImportError, ValueError):
                    found = False
                if not found:
                    missing.append(f"python module '{mod}' is not installed")
            if missing:
                c.state = AvailabilityState.DEPENDENCY_MISSING
                c.problems.extend(missing)

    def _healthcheck(self, c: RegisteredCapability) -> None:
        c.checked_at = iso_utc()
        try:
            result = run_with_timeout(c.manifest.handlers["healthcheck"], (), self.healthcheck_timeout_s)
        except TimeoutError as exc:
            c.state, c.problems = AvailabilityState.UNHEALTHY, [f"healthcheck timed out: {exc}"]
            return
        except Exception as exc:  # noqa: BLE001
            c.state, c.problems = AvailabilityState.UNHEALTHY, [f"healthcheck raised {type(exc).__name__}: {exc}"]
            return
        healthy = result.get("healthy") is True if isinstance(result, dict) else result is True
        c.health_detail = result
        if not healthy:
            c.state = AvailabilityState.UNHEALTHY
            c.problems.append(f"healthcheck reported unhealthy: {result!r}")

    def recheck_health(self) -> list[CapabilityAvailability]:
        with self._lock:
            caps = list(self._caps.values())
        for c in caps:
            if c.manifest and c.manifest.enabled and c.state in (AvailabilityState.AVAILABLE,
                                                                 AvailabilityState.UNHEALTHY):
                c.state, c.problems = AvailabilityState.AVAILABLE, []
                self._healthcheck(c)
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

    # ---------------------------------------------------------------- execution
    def execute(self, cap_id: str, payload: dict, ctx: RequestContext, timeout_s: float | None = None
                ) -> ExecutionResult:
        cap = self.get(cap_id)
        decision = self.policy.evaluate(PolicyRequest(
            principal=ctx.principal, operation="capability.execute", resource=cap_id,
            risk=cap.manifest.risk if cap and cap.manifest else None,
            required_permissions=cap.manifest.permissions if cap and cap.manifest else frozenset()))
        if not decision.allowed:
            raise ApiError(ErrorCode.PERMISSION_DENIED, decision.reason, details={"operation": "capability.execute",
                                                                                  "resource": cap_id})
        if cap is None:
            raise ApiError(ErrorCode.NOT_FOUND, f"capability '{cap_id}' is not registered")
        if not cap.executable:
            raise CapabilityNotExecutable(cap_id, f"{cap.state.value}: {'; '.join(cap.problems)}")
        m = cap.manifest
        errs = [e.message for e in Draft202012Validator(m.input_schema).iter_errors(payload)]
        if errs:
            raise ApiError(ErrorCode.VALIDATION_FAILED, "input does not match the capability input schema",
                           details=errs)
        limit = min(timeout_s or m.timeout_s, m.timeout_s)
        start = time.perf_counter()
        try:
            output = run_with_timeout(m.handlers["execute"], (payload, ctx), limit)
        except TimeoutError as exc:
            raise ApiError(ErrorCode.TIMEOUT, f"capability '{cap_id}' {exc}") from exc
        duration = (time.perf_counter() - start) * 1000
        if not isinstance(output, dict):
            raise ApiError(ErrorCode.INTERNAL, f"capability '{cap_id}' returned {type(output).__name__}, not object")
        errs = [e.message for e in Draft202012Validator(m.output_schema).iter_errors(output)]
        if errs:
            raise ApiError(ErrorCode.INTERNAL, "capability output failed its output schema", details=errs)
        verdict = run_with_timeout(m.handlers["verify"], (payload, output), self.healthcheck_timeout_s)
        verified = verdict.get("verified") is True if isinstance(verdict, dict) else verdict is True
        return ExecutionResult(capability_id=cap_id, output=output, verified=verified, verification_detail=verdict,
                               duration_ms=round(duration, 2))
