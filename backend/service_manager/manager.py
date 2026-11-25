"""The central service manager.

Responsibilities (in order): load configuration, validate required dependencies, detect available services,
resolve startup order, start services, check their health, discover/validate/register capabilities, report
unavailable ones, monitor for failures with bounded restarts, and shut everything down cleanly.

Essential services gate readiness: if any essential service is not healthy, the manager's state is NOT_READY or
FAILED and `ready` is false — it never reports readiness it has not measured.
"""
from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
import sys
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

import psutil

from backend.capability_registry import CapabilityRegistry
from backend.contracts import DependencyHealth, HealthStatus, get_logger, iso_utc
from backend.settings import REPO_ROOT, ServiceSpec, Settings
from backend.settings.loader import resolve_path

from .probes import probe_service, render

log = get_logger("atta.service_manager")


class ManagerState(str, Enum):
    STARTING = "starting"
    READY = "ready"
    DEGRADED = "degraded"      # every essential service healthy; one or more optional services are not
    NOT_READY = "not_ready"    # an essential service is currently unhealthy (may recover)
    FAILED = "failed"          # an essential service failed permanently (restart budget exhausted / start failed)
    STOPPING = "stopping"
    STOPPED = "stopped"


READY_STATES = (ManagerState.READY, ManagerState.DEGRADED)


class StartupFailed(RuntimeError):
    def __init__(self, reasons: list[str]):
        super().__init__("; ".join(reasons))
        self.reasons = reasons


@dataclass
class ServiceRuntime:
    name: str
    spec: ServiceSpec
    status: str = "pending"    # pending|starting|healthy|unhealthy|restarting|failed|stopped|disabled|unavailable
    process: subprocess.Popen | None = None
    restarts: deque = field(default_factory=deque)
    restart_total: int = 0
    consecutive_failures: int = 0
    last_health: DependencyHealth | None = None
    detail: str = ""

    @property
    def pid(self) -> int | None:
        return self.process.pid if self.process else None

    HEALTH_STATES = {"pending": "STARTING", "starting": "STARTING", "healthy": "HEALTHY", "restarting": "DEGRADED",
                     "unhealthy": "UNHEALTHY", "failed": "UNHEALTHY", "stopped": "STOPPED", "disabled": "STOPPED",
                     "unavailable": "BLOCKED"}

    def to_dict(self) -> dict:
        return {"name": self.name, "essential": self.spec.essential, "mode": self.spec.mode, "status": self.status,
                "health_state": self.HEALTH_STATES.get(self.status, "UNHEALTHY"),
                "pid": self.pid, "restarts": self.restart_total, "detail": self.detail,
                "health": self.last_health.model_dump(mode="json") if self.last_health else None}


class ServiceManager:
    FAILURE_THRESHOLD = 2  # consecutive failed probes before a managed service is treated as failed

    def __init__(self, settings: Settings, root: Path = REPO_ROOT, python: str = sys.executable):
        self.settings, self.root, self.python = settings, root, python
        self.runtime_dir = resolve_path(settings.runtime.directory, root)
        self.state_dir = self.runtime_dir / "state"
        self.log_dir = resolve_path(settings.logging.directory, root)
        self.state_file = self.state_dir / "manager.json"
        self.pid_file = self.state_dir / "manager.pid"
        self.stop_request = self.state_dir / "stop.request"
        self.services = {n: ServiceRuntime(n, s) for n, s in settings.services.items()}
        self.state = ManagerState.STOPPED
        self.reasons: list[str] = []
        self.model_health: list[DependencyHealth] = []
        self.registry: CapabilityRegistry | None = None
        self.capability_report: list = []
        self.started_at: str | None = None
        self._stop = threading.Event()
        self._lock = threading.RLock()

    # ------------------------------------------------------------------ 2. validation
    def validate_dependencies(self) -> list[str]:
        problems = []
        if sys.version_info < (3, 12):
            problems.append(f"Python >= 3.12 required, found {sys.version.split()[0]}")
        for rt in self.services.values():
            s = rt.spec
            if s.mode == "managed" and s.requires_binary and shutil.which(s.requires_binary) is None:
                msg = f"service '{rt.name}' needs '{s.requires_binary}' on PATH"
                if s.essential:
                    problems.append(msg)
                else:
                    rt.status, rt.detail = "unavailable", msg
        for mod in ("fastapi", "uvicorn", "psycopg", "redis", "jsonschema", "yaml", "psutil"):
            try:
                __import__(mod)
            except ImportError:
                problems.append(f"python package '{mod}' missing — run INSTALL")
        try:
            self.state_dir.mkdir(parents=True, exist_ok=True)
            self.log_dir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            problems.append(f"runtime directory not writable: {exc}")
        try:
            self.startup_order()
        except ValueError as exc:
            problems.append(str(exc))
        return problems

    # ------------------------------------------------------------------ 3. detection
    def detect_services(self) -> dict[str, DependencyHealth]:
        out = {}
        for name, rt in self.services.items():
            if rt.spec.mode == "disabled":
                rt.status = "disabled"
                continue
            out[name] = probe_service(name, rt.spec.essential, rt.spec.health, self.settings)
        from backend.model_gateway import endpoint_health
        self.model_health = endpoint_health(self.settings)
        return out

    # ------------------------------------------------------------------ 4. ordering
    def startup_order(self) -> list[str]:
        order, temp, perm = [], set(), set()

        def visit(n: str, chain: list[str]):
            if n in perm:
                return
            if n in temp:
                raise ValueError("service dependency cycle: " + " -> ".join(chain + [n]))
            temp.add(n)
            for d in self.settings.services[n].depends_on:
                visit(d, chain + [n])
            temp.discard(n)
            perm.add(n)
            order.append(n)

        for n in sorted(self.settings.services):
            visit(n, [])
        return order

    # ------------------------------------------------------------------ 5/6. start + health
    def _command(self, spec: ServiceSpec) -> list[str]:
        extra = {"python": self.python, "repo_root": str(self.root), "runtime_dir": str(self.runtime_dir)}
        return [render(part, self.settings, extra) for part in spec.command]

    def _spawn(self, rt: ServiceRuntime) -> None:
        cmd = self._command(rt.spec)
        if rt.name == "redis":
            (self.runtime_dir / "redis").mkdir(parents=True, exist_ok=True)
        env = dict(os.environ)
        env.update(rt.spec.env)
        env["PYTHONPATH"] = str(self.root) + os.pathsep + env.get("PYTHONPATH", "")
        env["ATTA_ENV"] = self.settings.environment.value
        env["ATTA_MANAGED_BY"] = str(os.getpid())
        logf = open(self.log_dir / f"{rt.name}.log", "ab")
        kwargs: dict = {"cwd": str(self.root), "env": env, "stdout": logf, "stderr": subprocess.STDOUT,
                        "stdin": subprocess.DEVNULL}
        if os.name == "nt":
            kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP  # type: ignore[attr-defined]
        else:
            kwargs["start_new_session"] = True
        rt.process = subprocess.Popen(cmd, **kwargs)
        logf.close()
        log.info("spawned %s pid=%s", rt.name, rt.process.pid, extra={"service": rt.name, "command": cmd})

    def _wait_healthy(self, rt: ServiceRuntime, timeout: float) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline and not self._stop.is_set():
            if rt.process is not None and rt.process.poll() is not None:
                rt.detail = f"process exited with code {rt.process.returncode} (see {self.log_dir / (rt.name + '.log')})"
                return False
            h = probe_service(rt.name, rt.spec.essential, rt.spec.health, self.settings)
            rt.last_health = h
            if h.status is HealthStatus.HEALTHY:
                rt.detail = h.detail or ""
                return True
            rt.detail = h.detail or ""
            time.sleep(0.25)
        return False

    def _probe_once(self, rt: ServiceRuntime) -> bool:
        h = probe_service(rt.name, rt.spec.essential, rt.spec.health, self.settings)
        rt.last_health, rt.detail = h, h.detail or ""
        return h.status is HealthStatus.HEALTHY

    def _start_service(self, rt: ServiceRuntime) -> bool:
        spec = rt.spec
        if spec.mode == "disabled":
            rt.status = "disabled"
            return True
        if rt.status == "unavailable":
            return not spec.essential
        for dep in spec.depends_on:
            if self.services[dep].status not in ("healthy", "disabled") and self.settings.services[dep].essential:
                rt.status, rt.detail = "failed", f"dependency '{dep}' is {self.services[dep].status}"
                return not spec.essential
        rt.status = "starting"
        if spec.mode == "managed":
            pre = probe_service(rt.name, spec.essential, spec.health, self.settings)
            if pre.status is HealthStatus.HEALTHY:
                rt.status = "failed"
                rt.detail = ("endpoint already answering before start — another, unmanaged process holds it; "
                             "stop it (STOP / RECOVERY) or change the port")
                return not spec.essential
            self._spawn(rt)
        # Optional external services get one probe: their absence must not delay startup. Essential and managed
        # services are waited for up to their startup timeout.
        wait = spec.startup_timeout_s if (spec.essential or spec.mode == "managed") else 0.0
        ok = self._wait_healthy(rt, wait) if wait else self._probe_once(rt)
        rt.status = "healthy" if ok else ("failed" if spec.essential or spec.mode == "managed" else "unhealthy")
        if not ok and spec.mode == "managed":
            self._terminate(rt)
            rt.status = "failed"
        log.info("service %s -> %s", rt.name, rt.status, extra={"service": rt.name, "detail": rt.detail})
        return ok or not spec.essential

    # ------------------------------------------------------------------ 7-10. capabilities
    def _service_available(self, name: str) -> bool:
        rt = self.services.get(name)
        return rt is not None and rt.status == "healthy"

    def discover_capabilities(self) -> list:
        from backend.capability_registry import TrustStore
        dirs = [resolve_path(d, self.root) for d in self.settings.capabilities.directories]
        trust = TrustStore.load(resolve_path(self.settings.capabilities.trust_store, self.root),
                                resolve_path(self.settings.capabilities.trust_public_key, self.root))
        self.registry = CapabilityRegistry(dirs, service_available=self._service_available, trust=trust,
                                           healthcheck_timeout_s=self.settings.capabilities.healthcheck_timeout_s,
                                           max_timeout_s=self.settings.deadlines.max_execution_timeout_s)
        self.capability_report = self.registry.discover()
        for a in self.capability_report:
            if not a.executable:
                log.warning("capability unavailable: %s (%s) %s", a.id, a.state.value, "; ".join(a.problems))
        return self.capability_report

    # ------------------------------------------------------------------ lifecycle
    def start(self) -> None:
        self.started_at = iso_utc()
        self._set_state(ManagerState.STARTING)
        self._write_pid()
        problems = self.validate_dependencies()
        if problems:
            self.reasons = problems
            self._set_state(ManagerState.FAILED)
            raise StartupFailed(problems)
        self.detect_services()
        for name in self.startup_order():
            rt = self.services[name]
            if not self._start_service(rt):
                self.reasons = [f"essential service '{name}' failed to start: {rt.detail}"]
                log.error(self.reasons[0])
                self.discover_capabilities()
                self.shutdown(final_state=ManagerState.FAILED)
                raise StartupFailed(self.reasons)
            self.write_state()
        self.discover_capabilities()
        self._evaluate()

    def _evaluate(self) -> None:
        reasons, failed = [], False
        for rt in self.services.values():
            if rt.spec.essential and rt.status != "healthy" and rt.status != "disabled":
                reasons.append(f"essential service '{rt.name}' is {rt.status}: {rt.detail}")
                failed |= rt.status == "failed"
        optional_bad = any(not rt.spec.essential and rt.status not in ("healthy", "disabled")
                           for rt in self.services.values())
        self.reasons = reasons
        if reasons:
            self._set_state(ManagerState.FAILED if failed else ManagerState.NOT_READY)
        else:
            self._set_state(ManagerState.DEGRADED if optional_bad else ManagerState.READY)

    def monitor_once(self) -> None:
        for name in self.startup_order():
            rt = self.services[name]
            if rt.spec.mode == "disabled" or rt.status in ("failed", "unavailable", "disabled"):
                continue
            exited = rt.process is not None and rt.process.poll() is not None
            h = probe_service(rt.name, rt.spec.essential, rt.spec.health, self.settings)
            rt.last_health = h
            if not exited and h.status is HealthStatus.HEALTHY:
                rt.consecutive_failures, rt.status, rt.detail = 0, "healthy", h.detail or ""
                continue
            rt.consecutive_failures += 1
            rt.detail = f"process exited ({rt.process.returncode})" if exited else (h.detail or "unhealthy")
            if rt.spec.mode == "external":
                rt.status = "unhealthy"
                continue
            if exited or rt.consecutive_failures >= self.FAILURE_THRESHOLD:
                self._restart(rt)
        if self.registry is not None:   # correction 3: capabilities follow CURRENT service health
            self.capability_report = self.registry.refresh_dependencies()
        self._evaluate()

    def _restart(self, rt: ServiceRuntime) -> None:
        now = time.monotonic()
        while rt.restarts and now - rt.restarts[0] > rt.spec.restart_window_s:
            rt.restarts.popleft()
        self._terminate(rt)
        if len(rt.restarts) >= rt.spec.max_restarts:
            rt.status = "failed"
            rt.detail = f"{rt.detail}; restart budget exhausted ({rt.spec.max_restarts} in {rt.spec.restart_window_s}s)"
            log.error("service %s failed permanently: %s", rt.name, rt.detail)
            return
        rt.restarts.append(now)
        rt.restart_total += 1
        rt.status = "restarting"
        self.write_state()
        delay = min(30.0, rt.spec.restart_backoff_s * (2 ** (len(rt.restarts) - 1)))
        log.warning("restarting %s (attempt %d/%d) in %.1fs", rt.name, len(rt.restarts), rt.spec.max_restarts, delay)
        if self._stop.wait(delay):
            return
        self._spawn(rt)
        ok = self._wait_healthy(rt, rt.spec.startup_timeout_s)
        rt.consecutive_failures = 0 if ok else rt.consecutive_failures
        rt.status = "healthy" if ok else "unhealthy"

    def run_forever(self) -> int:
        self._install_signal_handlers()
        try:
            self.start()
        except StartupFailed as exc:
            log.error("startup failed: %s", exc)
            return 2
        self.write_state()
        interval = self.settings.runtime.monitor_interval_s
        while not self._stop.wait(interval):
            if self.stop_request.exists():
                log.info("stop requested")
                break
            self.monitor_once()
            self.write_state()
        self.shutdown()
        return 0

    def request_stop(self) -> None:
        self._stop.set()

    def _install_signal_handlers(self) -> None:
        def handler(signum, _frame):
            log.info("signal %s received, shutting down", signum)
            self._stop.set()
        for sig in (signal.SIGINT, signal.SIGTERM, getattr(signal, "SIGBREAK", None)):
            if sig is not None:
                try:
                    signal.signal(sig, handler)
                except (ValueError, OSError):
                    pass

    # ------------------------------------------------------------------ 13. shutdown
    def _terminate(self, rt: ServiceRuntime, grace: float = 10.0) -> None:
        p = rt.process
        if p is None:
            return
        if p.poll() is None:
            try:
                proc = psutil.Process(p.pid)
                children = proc.children(recursive=True)
            except psutil.Error:
                children = []
            try:
                if os.name == "nt":
                    p.terminate()
                else:
                    os.killpg(p.pid, signal.SIGTERM)
            except (ProcessLookupError, PermissionError, OSError):
                pass
            try:
                p.wait(grace)
            except subprocess.TimeoutExpired:
                for c in children:
                    try:
                        c.kill()
                    except psutil.Error:
                        pass
                p.kill()
                p.wait(5)
            for c in children:
                try:
                    c.wait(2)
                except psutil.Error:
                    pass
        rt.process = None

    def shutdown(self, final_state: ManagerState = ManagerState.STOPPED) -> None:
        self._set_state(ManagerState.STOPPING)
        for name in reversed(self.startup_order()):
            rt = self.services[name]
            if rt.process is not None:
                log.info("stopping %s", name)
                self._terminate(rt)
                rt.status = "stopped"
        self._set_state(final_state)
        self.write_state()
        for f in (self.pid_file, self.stop_request):
            try:
                f.unlink()
            except FileNotFoundError:
                pass

    # ------------------------------------------------------------------ state
    def _set_state(self, state: ManagerState) -> None:
        with self._lock:
            if state != self.state:
                log.info("manager state %s -> %s", self.state.value, state.value)
            self.state = state

    def _write_pid(self) -> None:
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.pid_file.write_text(str(os.getpid()))
        try:
            self.stop_request.unlink()
        except FileNotFoundError:
            pass

    def snapshot(self) -> dict:
        return {
            "manager_pid": os.getpid(), "state": self.state.value, "ready": self.state in READY_STATES,
            "environment": self.settings.environment.value, "started_at": self.started_at, "updated_at": iso_utc(),
            "reasons": self.reasons, "startup_order": self.startup_order(),
            "services": {n: rt.to_dict() for n, rt in self.services.items()},
            "models": [h.model_dump(mode="json") for h in self.model_health],
            "capabilities": [a.model_dump(mode="json") for a in self.capability_report],
        }

    def write_state(self) -> None:
        self.state_dir.mkdir(parents=True, exist_ok=True)
        tmp = self.state_file.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.snapshot(), indent=2))
        os.replace(tmp, self.state_file)
