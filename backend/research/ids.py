"""Stage 11 identifiers: '<prefix>_<ULID>' using the single ULID implementation of backend.contracts.identifiers.

Research prefixes are kept here instead of widening the shared ID_PREFIXES set, so Stage 11 merges without editing
a shared contract file (STAGE_11_MERGE_MANIFEST.md, "shared files not modified")."""
from __future__ import annotations

import re

from backend.contracts.identifiers import _ulid

RESEARCH_PREFIXES = frozenset({"rsr", "rqy", "src", "snp", "rev", "clm", "cit", "cdn", "rck", "ret"})
_RE = re.compile(r"^(rsr|rqy|src|snp|rev|clm|cit|cdn|rck|ret)_[0-9A-HJKMNP-TV-Z]{26}$")


def rid(prefix: str) -> str:
    if prefix not in RESEARCH_PREFIXES:
        raise ValueError(f"unknown research identifier prefix {prefix!r}")
    return f"{prefix}_{_ulid()}"


def is_rid(value: str, prefix: str | None = None) -> bool:
    return bool(_RE.match(value or "")) and (prefix is None or value.startswith(prefix + "_"))
