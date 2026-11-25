"""Unique identifiers: '<prefix>_<26-char ULID>' — sortable by creation time, globally unique, type-tagged."""
from __future__ import annotations

import os
import re
import time

_CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
ID_PREFIXES = frozenset({
    "usr", "ten", "req", "cor", "tsk", "evt", "evd", "cap", "svc", "idm", "chk", "run", "ses", "prj", "fil", "msg",
    "exe", "apv", "aud", "obj", "prp",
})
_ID_RE = re.compile(r"^([a-z]{3})_([0-9A-HJKMNP-TV-Z]{26})$")


def _ulid() -> str:
    ms = int(time.time() * 1000)
    value = (ms << 80) | int.from_bytes(os.urandom(10), "big")
    out = []
    for _ in range(26):
        out.append(_CROCKFORD[value & 31])
        value >>= 5
    return "".join(reversed(out))


def new_id(prefix: str) -> str:
    if prefix not in ID_PREFIXES:
        raise ValueError(f"unknown identifier prefix {prefix!r}")
    return f"{prefix}_{_ulid()}"


def is_valid_id(value: str, prefix: str | None = None) -> bool:
    m = _ID_RE.match(value or "")
    if not m or m.group(1) not in ID_PREFIXES:
        return False
    return prefix is None or m.group(1) == prefix


def parse_id(value: str) -> tuple[str, int]:
    """Return (prefix, creation-time-ms). Raises ValueError for malformed identifiers."""
    m = _ID_RE.match(value or "")
    if not m or m.group(1) not in ID_PREFIXES:
        raise ValueError(f"malformed identifier {value!r}")
    n = 0
    for ch in m.group(2):
        n = n * 32 + _CROCKFORD.index(ch)
    return m.group(1), n >> 80
