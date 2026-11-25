"""Standardised API errors: every non-2xx response body is an ErrorEnvelope."""
from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel


class ErrorCode(str, Enum):
    VALIDATION_FAILED = "validation_failed"
    NOT_FOUND = "not_found"
    UNAUTHENTICATED = "unauthenticated"
    PERMISSION_DENIED = "permission_denied"
    CONFLICT = "conflict"
    IDEMPOTENCY_CONFLICT = "idempotency_conflict"
    NOT_READY = "not_ready"
    CAPABILITY_UNAVAILABLE = "capability_unavailable"
    TIMEOUT = "timeout"
    RATE_LIMITED = "rate_limited"
    INTERNAL = "internal_error"
    APPROVAL_INVALID = "approval_invalid"
    EXECUTION_IN_PROGRESS = "execution_in_progress"
    OUTCOME_UNKNOWN = "outcome_unknown"
    VERIFICATION_FAILED = "verification_failed"
    EXECUTION_FAILED = "execution_failed"
    CANCELLED = "cancelled"


HTTP_STATUS = {
    ErrorCode.VALIDATION_FAILED: 422, ErrorCode.NOT_FOUND: 404, ErrorCode.UNAUTHENTICATED: 401,
    ErrorCode.PERMISSION_DENIED: 403, ErrorCode.CONFLICT: 409, ErrorCode.IDEMPOTENCY_CONFLICT: 409,
    ErrorCode.NOT_READY: 503, ErrorCode.CAPABILITY_UNAVAILABLE: 503, ErrorCode.TIMEOUT: 504,
    ErrorCode.RATE_LIMITED: 429, ErrorCode.INTERNAL: 500,
    ErrorCode.APPROVAL_INVALID: 409, ErrorCode.EXECUTION_IN_PROGRESS: 409,
    ErrorCode.OUTCOME_UNKNOWN: 409, ErrorCode.VERIFICATION_FAILED: 502, ErrorCode.EXECUTION_FAILED: 502,
    ErrorCode.CANCELLED: 409,
}


class ErrorBody(BaseModel):
    code: ErrorCode
    message: str
    correlation_id: str | None = None
    details: dict[str, Any] | list[Any] | None = None


class ErrorEnvelope(BaseModel):
    error: ErrorBody


class ApiError(Exception):
    def __init__(self, code: ErrorCode, message: str, details: Any = None, status: int | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details
        self.status = status or HTTP_STATUS[code]

    def envelope(self, correlation_id: str | None) -> dict:
        return ErrorEnvelope(error=ErrorBody(code=self.code, message=self.message, correlation_id=correlation_id,
                                             details=self.details)).model_dump(mode="json")
