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


class CapabilityRuntime:
    def __init__(self, *, python: str = sys.executable, memory_limit_mb: int = 1024, attestor: Attestor | None = None):
        self.python, self.memory_limit_mb = python, memory_limit_mb
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
            tool_handlers: list | None = None) -> RunResult:
        self._check_ticket(ticket, capability_id, mode)
        own_tmp = None
        if workdir is None:
            own_tmp = tempfile.TemporaryDirectory(prefix="atta-run-")
            workdir = Path(own_tmp.name)
        req = {"mode": mode, "capability_id": capability_id, "package_dir": str(package_dir),
               "expected_digest": expected_digest, "handlers": handlers, "payload": payload, "output": output,
               "context": context or {}, "timeout_s": timeout_s, "tool_handlers": tool_handlers or [], "workdir": str(workdir),
               "memory_limit_mb": self.memory_limit_mb, "cpu_seconds": int(timeout_s) + 2}
        env = {"PATH": os.environ.get("PATH", os.defpath), "LANG": "C.UTF-8", "PYTHONDONTWRITEBYTECODE": "1"}
        kwargs: dict = {"stdin": subprocess.PIPE, "stdout": subprocess.PIPE, "stderr": subprocess.PIPE, "env": env,
                        "cwd": str(workdir), "text": True}
        if os.name == "nt":
            kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP  # type: ignore[attr-defined]
        else:
            kwargs["start_new_session"] = True
        t0 = time.monotonic()
        proc = subprocess.Popen([self.python, "-I", str(SUPERVISOR)], **kwargs)
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
        return RunResult(status=data.get("status", "crashed"), value=data.get("value"), error=data.get("error"),
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
