"""Stage 11 research error model (§35). Every failure carries one of these codes — never a generic 'research failed'."""
from __future__ import annotations

from enum import Enum


class ResearchErrorCode(str, Enum):
    PROVIDER_UNAVAILABLE = "PROVIDER_UNAVAILABLE"      # no configured/working search provider
    OFFLINE_UNAVAILABLE = "OFFLINE_UNAVAILABLE"        # external research requested while OFFLINE / AIR_GAPPED
    NETWORK_DENIED = "NETWORK_DENIED"                  # destination refused (SSRF, policy, domain restriction, redirect)
    SOURCE_UNAVAILABLE = "SOURCE_UNAVAILABLE"          # DNS/connect/TLS/HTTP error, timeout
    SOURCE_TOO_LARGE = "SOURCE_TOO_LARGE"              # declared/streamed/decompressed size above the policy cap
    UNSUPPORTED_CONTENT = "UNSUPPORTED_CONTENT"        # content type or encoding not accepted
    EXTRACTION_FAILED = "EXTRACTION_FAILED"            # content could not be parsed into a typed extraction
    BUDGET_EXHAUSTED = "BUDGET_EXHAUSTED"              # a research budget (queries, sources, fetches, ...) ran out
    CANCELLED = "CANCELLED"                            # cancelled by request; never reported as completed
    VERIFICATION_FAILED = "VERIFICATION_FAILED"        # independent research verification did not pass
    BLOCKED_DEPENDENCY = "BLOCKED_DEPENDENCY"          # needs a stage/dependency that is not integrated


class ResearchError(Exception):
    def __init__(self, code: ResearchErrorCode, message: str, details: dict | None = None):
        super().__init__(f"{code.value}: {message}")
        self.code, self.message, self.details = code, message, details or {}

    def as_dict(self) -> dict:
        return {"code": self.code.value, "message": self.message, "details": self.details}


def code_of(value: str | None) -> ResearchErrorCode | None:
    if value is None:
        return None
    try:
        return ResearchErrorCode(value)
    except ValueError:
        return ResearchErrorCode.SOURCE_UNAVAILABLE
