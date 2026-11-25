"""Supervised, isolated execution of capability handlers (corrections 1 and 4, Stage 01A §16).

API/controller process ──(JSON over pipes)──> supervisor.py ──> child.py
  * Nothing from a capability package is ever imported into the API or controller process.
  * supervisor.py (stdlib + psutil) runs in its own session and makes itself a child SUBREAPER (Linux), so every
    descendant the handler starts — including double-forked, setsid() daemons — stays its descendant and is
    killed when the handler finishes, times out, is cancelled or when the controller dies.
  * child.py re-reads the package, recomputes its digest over the exact bytes it will execute, refuses on any
    mismatch, applies resource limits and loads handlers from that in-memory snapshot only.
"""
from .runner import CapabilityRuntime, ExecutionTicket, RunResult, RuntimeRefused

__all__ = ["CapabilityRuntime", "RunResult", "ExecutionTicket", "RuntimeRefused"]
