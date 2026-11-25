"""Plugin isolation contract (Stage 12 §22) — prepared for Stage 01B environment isolation.

The Stage 01A supervised runtime provides: a separate `python -I` process, a stripped environment (no ATTa secrets,
no database credentials in the environment), resource limits, whole-process-tree termination, package-integrity
re-verification and the task workspace as working directory. It does NOT provide filesystem, network, user or
tenant isolation. RESTRICTED/UNTRUSTED integrations and ISOLATED_SANDBOX execution therefore require an
IsolationProvider; when none is available they are BLOCKED — isolation is never claimed without one."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class IsolationReport:
    available: bool
    provider: str
    guarantees: tuple[str, ...]
    reason: str


class IsolationProvider(Protocol):
    def report(self) -> IsolationReport: ...


SUPERVISED_GUARANTEES = ("separate interpreter process (python -I)", "stripped environment (no ATTa secrets)",
                         "rlimits (memory, cpu, file size, open files, no core)", "process-tree kill",
                         "package bytes re-verified before execution", "task workspace working directory")
REQUIRED_SANDBOX_GUARANTEES = ("filesystem confinement to the task workspace", "no network unless allowed",
                               "unprivileged user", "no access to other tasks/tenants", "no ATTa secret files")


class NoOsSandbox:
    """Default until Stage 01B: reports isolation UNAVAILABLE (honest BLOCKED)."""

    def report(self) -> IsolationReport:
        return IsolationReport(False, "none", SUPERVISED_GUARANTEES,
                               "BLOCKED: no OS sandbox provider (Stage 01B environment isolation not merged)")
