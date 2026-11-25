"""Controller/API side of isolated execution: spawn, deadline, cancellation, whole-tree termination, evidence."""
from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import tempfile
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import psutil

from backend.authentication.attestation import Attestor

SUPERVISOR = Path(__file__).with_name("supervisor.py")


class RuntimeRefused(PermissionError):
    pass


@dataclass(frozen=True)
class ExecutionTicket:
    """Issued only by the execution gateway (or the registry for probe/health); verified before any process starts."""
    execution_id: str
    capability_id: str
    mode: str
    expires_at: float
    signature: str


@dataclass
class RunResult:
    status: str                      # ok | error | timeout | cancelled | crashed | integrity_failed
    value: Any = None
    error: str | None = None
    error_type: str | None = None
    duration_s: float = 0.0
    leftovers_killed: int = 0
    survivors: list[int] = field(default_factory=list)   # must always be empty
    stderr_tail: str = ""
    subreaper: bool = False
    isolation: dict = field(default_factory=dict)


class CapabilityRuntime:
    def __init__(self, *, python: str = sys.executable, memory_limit_mb: int = 1024, attestor: Attestor | None = None,
                 network: str = "host", worker_user: str | None = None, cuda_visible_devices: str | None = None,
                 isolation_required: bool = False):
        """network: 'none' gives each handler a private empty network namespace (Linux, privileged); worker_user drops
        the handler to an unprivileged account; GPUs are hidden from handlers unless cuda_visible_devices is set."""
        self.python, self.memory_limit_mb = python, memory_limit_mb
        self.network, self.worker_user, self.cuda_visible_devices = network, worker_user, cuda_visible_devices
        self.isolation_required = isolation_required
        self._attestor = attestor or Attestor()
        self._gateway: Attestor | None = None
        self._running: dict[str, subprocess.Popen] = {}
        self._cancelled: set[str] = set()
        self._lock = threading.Lock()

    # ---------------------------------------------------------------- tickets
    INTERNAL_MODES = frozenset({"probe", "healthcheck", "verify", "reconcile"})

    def bind_gateway(self, gateway_attestor: Attestor) -> None:
        """The execution gateway registers its private attestor once; only it can sign `execute` tickets."""
        with self._lock:
            if self._gateway is not None and self._gateway is not gateway_attestor:
                raise RuntimeRefused("an execution gateway is already bound to this runtime")
            self._gateway = gateway_attestor

    def issue_ticket(self, execution_id: str, capability_id: str, mode: str, ttl_s: float = 60.0) -> ExecutionTicket:
        if mode not in self.INTERNAL_MODES:
            raise RuntimeRefused("execute tickets are issued only by the execution gateway")
        exp = time.time() + ttl_s
        return ExecutionTicket(execution_id, capability_id, mode, exp,
                               self._attestor.sign_fields(execution_id, capability_id, mode, exp))

    def _check_ticket(self, t: ExecutionTicket | None, capability_id: str, mode: str) -> None:
        signer = self._attestor if mode in self.INTERNAL_MODES else self._gateway
        if t is None or signer is None or t.capability_id != capability_id or t.mode != mode or \
                t.expires_at < time.time() or \
                not signer.verify_fields(t.signature, t.execution_id, t.capability_id, t.mode, t.expires_at):
            raise RuntimeRefused("no valid execution ticket: handlers run only through the execution gateway")

    # ---------------------------------------------------------------- run
    def run(self, *, ticket: ExecutionTicket, mode: str, capability_id: str, package_dir: Path, expected_digest: str,
            handlers: dict[str, str], timeout_s: float, payload: Any = None, output: Any = None,
            context: dict | None = None, workdir: Path | None = None,
            tool_handlers: list | None = None, network: str | None = None) -> RunResult:
        self._check_ticket(ticket, capability_id, mode)
        own_tmp = None
        if workdir is None:
            own_tmp = tempfile.TemporaryDirectory(prefix="atta-run-")
            workdir = Path(own_tmp.name)
        req = {"mode": mode, "capability_id": capability_id, "package_dir": str(package_dir),
               "expected_digest": expected_digest, "handlers": handlers, "payload": payload, "output": output,
               "context": context or {}, "timeout_s": timeout_s, "tool_handlers": tool_handlers or [], "workdir": str(workdir),
               "memory_limit_mb": self.memory_limit_mb, "cpu_seconds": int(timeout_s) + 2,
               "isolation": {"network": network or self.network, "user": self.worker_user,
                             "cuda_visible_devices": self.cuda_visible_devices, "memory_mb": self.memory_limit_mb,
                             "required": self.isolation_required}}
        env = {"PATH": os.environ.get("PATH", os.defpath), "LANG": "C.UTF-8", "PYTHONDONTWRITEBYTECODE": "1"}
        kwargs: dict = {"stdin": subprocess.PIPE, "stdout": subprocess.PIPE, "stderr": subprocess.PIPE, "env": env,
                        "cwd": str(workdir), "text": True}
        if os.name == "nt":
            kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP  # type: ignore[attr-defined]
        else:
            kwargs["start_new_session"] = True
        t0 = time.monotonic()
        proc = subprocess.Popen([self.python, "-I", str(SUPERVISOR)], **kwargs)
        job = _windows_job(proc) if os.name == "nt" else None
        with self._lock:
            self._running[ticket.execution_id] = proc
        out, deadline, sent, status = "", t0 + timeout_s + 5.0, False, None
        try:
            while True:
                try:
                    o, _ = proc.communicate(None if sent else json.dumps(req), timeout=0.1)
                    out += o or ""
                    break
                except subprocess.TimeoutExpired:
                    sent = True
                    if ticket.execution_id in self._cancelled or time.monotonic() > deadline:
                        status = "cancelled" if ticket.execution_id in self._cancelled else "timeout"
                        break
        finally:
            with self._lock:
                self._running.pop(ticket.execution_id, None)
        survivors: list[int] = []
        if status is not None:
            if job is not None:
                _windows_kill_job(job)
            survivors = self._terminate_tree(proc)
            try:
                o, _ = proc.communicate(timeout=2)
                out += o or ""
            except (subprocess.TimeoutExpired, ValueError):
                pass
        lines = [ln for ln in out.splitlines() if ln.strip()]
        try:
            data = json.loads(lines[-1]) if lines else {"status": status or "crashed"}
        except json.JSONDecodeError:
            data = {"status": "crashed", "error": "unparseable supervisor output"}
        if status is not None:
            data["status"] = status
        if own_tmp is not None:
            own_tmp.cleanup()
        if job is not None:
            _windows_kill_job(job)     # everything left in the job dies with it (KILL_ON_JOB_CLOSE)
        return RunResult(status=data.get("status", "crashed"), value=data.get("value"), error=data.get("error"),
                         isolation=data.get("isolation") or {},
                         error_type=data.get("error_type"), duration_s=round(time.monotonic() - t0, 3),
                         leftovers_killed=int(data.get("leftovers_killed", 0)), survivors=survivors,
                         stderr_tail=data.get("stderr_tail", ""), subreaper=bool(data.get("subreaper")))

    def cancel(self, execution_id: str) -> bool:
        with self._lock:
            self._cancelled.add(execution_id)
            return execution_id in self._running

    def _terminate_tree(self, proc: subprocess.Popen) -> list[int]:
        """SIGTERM the supervisor (it kills its subtree), then SIGKILL the session and every remaining descendant.
        Returns the PIDs still alive afterwards (always expected to be empty)."""
        try:
            tree = psutil.Process(proc.pid).children(recursive=True)
        except psutil.Error:
            tree = []
        try:
            proc.send_signal(signal.SIGTERM)
            proc.wait(2)
        except (subprocess.TimeoutExpired, ProcessLookupError, OSError):
            pass
        if os.name != "nt":
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError, OSError):
                pass
        for p in tree:
            try:
                p.kill()
            except psutil.Error:
                pass
        try:
            proc.kill()
            proc.wait(2)
        except (ProcessLookupError, OSError, subprocess.TimeoutExpired):
            pass
        alive = []
        for p in tree:
            try:
                if p.is_running() and p.status() != psutil.STATUS_ZOMBIE:
                    alive.append(p.pid)
            except psutil.Error:
                pass
        return alive


# ------------------------------------------------------------------------------------------------ Windows Job Objects
def _windows_job(proc):
    """Place the supervisor (and therefore every process it starts) in a Job Object with KILL_ON_JOB_CLOSE.
    Implemented with ctypes; NOT yet executed on Windows in this project's evidence (BLOCKERS B-015)."""
    import ctypes
    from ctypes import wintypes
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)

    class BASIC(ctypes.Structure):
        _fields_ = [("PerProcessUserTimeLimit", ctypes.c_int64), ("PerJobUserTimeLimit", ctypes.c_int64),
                    ("LimitFlags", wintypes.DWORD), ("MinimumWorkingSetSize", ctypes.c_size_t),
                    ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", wintypes.DWORD),
                    ("Affinity", ctypes.c_size_t), ("PriorityClass", wintypes.DWORD), ("SchedulingClass", wintypes.DWORD)]

    class IOC(ctypes.Structure):
        _fields_ = [(n, ctypes.c_uint64) for n in ("r", "w", "o", "rb", "wb", "ob")]

    class EXT(ctypes.Structure):
        _fields_ = [("BasicLimitInformation", BASIC), ("IoInfo", IOC), ("ProcessMemoryLimit", ctypes.c_size_t),
                    ("JobMemoryLimit", ctypes.c_size_t), ("PeakProcessMemoryUsed", ctypes.c_size_t),
                    ("PeakJobMemoryUsed", ctypes.c_size_t)]
    job = k32.CreateJobObjectW(None, None)
    info = EXT()
    info.BasicLimitInformation.LimitFlags = 0x2000   # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    k32.SetInformationJobObject(job, 9, ctypes.byref(info), ctypes.sizeof(info))   # ExtendedLimitInformation
    k32.AssignProcessToJobObject(job, wintypes.HANDLE(int(proc._handle)))
    return job


def _windows_kill_job(job) -> None:
    import ctypes
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.TerminateJobObject(job, 1)
    k32.CloseHandle(job)
