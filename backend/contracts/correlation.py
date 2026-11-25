"""Correlation identifiers propagate through logs, events and outbound calls via a context variable."""
from __future__ import annotations

import contextvars
import re

from .identifiers import new_id

CORRELATION_HEADER = "X-Correlation-ID"
_SAFE = re.compile(r"^[A-Za-z0-9_.:-]{8,128}$")
_current: contextvars.ContextVar[str | None] = contextvars.ContextVar("atta_correlation_id", default=None)


def new_correlation_id() -> str:
    return new_id("cor")


def set_correlation_id(value: str | None) -> str:
    """Accept a caller-supplied id only if it is well-formed; otherwise mint one (prevents log injection)."""
    cid = value if value and _SAFE.match(value) else new_correlation_id()
    _current.set(cid)
    return cid


def get_correlation_id() -> str | None:
    return _current.get()
