"""Operator commands around the manager process: detached start, stop, status and an independent healthcheck."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import httpx
import psutil

from backend.contracts import HealthStatus
from backend.settings import REPO_ROOT, Settings

from .manager import READY_STATES, ManagerState, ServiceManager


def _alive(pid: int | None) -> bool:
    if not pid:
        return False
    try:
        p = psutil.Process(pid)
        return p.is_running() and p.status() != psutil.STATUS_ZOMBIE
    except psutil.Error:
        return False


def read_state(mgr: ServiceManager) -> dict | None:
    try:
        return json.loads(mgr.state_file.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def manager_pid(mgr: ServiceManager) -> int | None:
    try:
        pid = int(mgr.pid_file.read_text().strip())
    except (FileNotFoundError, ValueError):
        return None
    return pid if _alive(pid) else None


def start_detached(settings: Settings, root: Path = REPO_ROOT, timeout: float = 120.0) -> tuple[int, dict | None]:
    mgr = ServiceManager(settings, root)
    existing = manager_pid(mgr)
    if existing:
        return 3, {"error": f"service manager already running (pid {existing})", **(read_state(mgr) or {})}
    mgr.state_dir.mkdir(parents=True, exist_ok=True)
    mgr.log_dir.mkdir(parents=True, exist_ok=True)
    try:
        mgr.state_file.unlink()
    except FileNotFoundError:
        pass
    env = dict(os.environ, ATTA_ENV=settings.environment.value,
               PYTHONPATH=str(root) + os.pathsep + os.environ.get("PYTHONPATH", ""))
    kwargs: dict = {"cwd": str(root), "env": env, "stdin": subprocess.DEVNULL,
                    "stdout": open(mgr.log_dir / "manager.log", "ab"), "stderr": subprocess.STDOUT}
    if os.name == "nt":
        kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS  # type: ignore
    else:
        kwargs["start_new_session"] = True
    proc = subprocess.Popen([sys.executable, "-m", "backend.service_manager", "run"], **kwargs)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        st = read_state(mgr)
        if st and st.get("manager_pid") == proc.pid and st["state"] not in (ManagerState.STARTING.value,):
            if st["state"] in [s.value for s in READY_STATES]:
                return 0, st
            if st["state"] in (ManagerState.FAILED.value, ManagerState.STOPPED.value) or proc.poll() is not None:
                return 1, st
            if st["state"] == ManagerState.NOT_READY.value:
                return 1, st
        if proc.poll() is not None:
            return 1, read_state(mgr) or {"error": f"manager exited with code {proc.returncode}; see manager.log"}
        time.sleep(0.3)
    return 1, {"error": "timed out waiting for the service manager", **(read_state(mgr) or {})}


def stop(settings: Settings, root: Path = REPO_ROOT, timeout: float = 60.0) -> tuple[int, str]:
    mgr = ServiceManager(settings, root)
    pid = manager_pid(mgr)
    st = read_state(mgr) or {}
    if not pid:
        leftovers = _kill_orphans(st)
        return 0, ("service manager is not running" + (f"; cleaned up orphaned pids {leftovers}" if leftovers else ""))
    mgr.stop_request.write_text("stop")
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline and _alive(pid):
        time.sleep(0.3)
    if _alive(pid):
        try:
            psutil.Process(pid).terminate()
            psutil.Process(pid).wait(10)
        except psutil.Error:
            pass
        killed = _kill_orphans(read_state(mgr) or st)
        return (0 if not _alive(pid) else 1), f"manager did not stop in time; forced termination (pids {killed})"
    leftovers = _kill_orphans(read_state(mgr) or st)
    return 0, "stopped cleanly" + (f"; cleaned up orphaned pids {leftovers}" if leftovers else "")


def _kill_orphans(state: dict) -> list[int]:
    killed = []
    for svc in (state.get("services") or {}).values():
        pid = svc.get("pid")
        if pid and _alive(pid):
            try:
                p = psutil.Process(pid)
                for c in p.children(recursive=True):
                    c.kill()
                p.kill()
                killed.append(pid)
            except psutil.Error:
                pass
    return killed


def _port_of(spec, settings: Settings) -> int | None:
    t = spec.health.type
    if t == "postgres":
        return settings.database.port
    if t == "redis":
        return settings.redis.port
    if t == "tcp":
        return spec.health.port
    if t == "http" and spec.health.url:
        from urllib.parse import urlparse
        from .probes import render
        return urlparse(render(spec.health.url, settings)).port
    return None


def healthcheck(settings: Settings, root: Path = REPO_ROOT) -> tuple[int, dict]:
    """Independent inspection of real services, ports, dependencies and capabilities."""
    mgr = ServiceManager(settings, root)
    report: dict = {"environment": settings.environment.value, "checks": []}

    def check(name: str, ok: bool, detail: str, essential: bool = True):
        report["checks"].append({"check": name, "ok": ok, "essential": essential, "detail": detail})

    check("python", sys.version_info >= (3, 12), sys.version.split()[0])
    for binary, essential in (("git", False), ("node", False), ("redis-server", settings.services.get("redis") is not None
                                                                  and settings.services["redis"].mode == "managed")):
        path = shutil.which(binary)
        check(f"binary:{binary}", path is not None, path or "not found on PATH", essential)
    pid = manager_pid(mgr)
    st = read_state(mgr)
    check("service_manager", pid is not None, f"pid {pid}, state {st.get('state') if st else 'unknown'}")
    probes = mgr.detect_services()
    for name, h in probes.items():
        mgr.services[name].status = "healthy" if h.status is HealthStatus.HEALTHY else "unhealthy"
        port = _port_of(mgr.services[name].spec, settings)
        check(f"service:{name}", h.status is HealthStatus.HEALTHY,
              (f"port {port}: " if port else "") + (h.detail or ""), mgr.services[name].spec.essential)
    base = f"http://{settings.api.host}:{settings.api.port}"
    for path in ("/v1/health/live", "/v1/health/ready", "/v1/health/controller"):
        try:
            r = httpx.get(base + path, timeout=3, trust_env=False)
            body = r.json()
            check(f"api:{path}", r.status_code == 200, f"HTTP {r.status_code} {json.dumps(body)[:300]}")
            if path.endswith("ready"):
                report["readiness"] = body
            if path.endswith("controller"):
                report["controller"] = body
                for name, comp in (body.get("components") or {}).items():
                    check(f"controller:{name}", comp["ok"], comp.get("detail", ""))
                check("controller:tasks", True, f"active {body.get('active_tasks')}, blocked {body.get('blocked_tasks')}",
                      False)
        except Exception as exc:  # noqa: BLE001
            check(f"api:{path}", False, f"{type(exc).__name__}: {exc}")
    caps = mgr.discover_capabilities()
    report["capabilities"] = [a.model_dump(mode="json") for a in caps]
    for a in caps:
        check(f"capability:{a.id}", a.executable, f"{a.state.value} {'; '.join(a.problems)}".strip(), False)
    report["models"] = [m.model_dump(mode="json") for m in mgr.model_health]
    for m in mgr.model_health:
        check(m.name, m.status is HealthStatus.HEALTHY, m.detail or "", False)
    for w in settings.warnings():
        check("configuration", False, w, False)
    report["ok"] = all(c["ok"] for c in report["checks"] if c["essential"])
    return (0 if report["ok"] else 1), report
