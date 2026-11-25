"""Verification evidence: what was run, when, with what exit code and output, plus a content hash."""
from __future__ import annotations

import hashlib
from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field

from .identifiers import new_id


class EvidenceOutcome(str, Enum):
    PASSED = "passed"
    FAILED = "failed"
    ERROR = "error"
    BLOCKED = "blocked"


class VerificationEvidence(BaseModel):
    id: str = Field(default_factory=lambda: new_id("evd"))
    subject: str
    command: list[str]
    cwd: str
    started_at: datetime
    finished_at: datetime
    exit_code: int | None
    outcome: EvidenceOutcome
    stdout_tail: str = ""
    stderr_tail: str = ""
    output_sha256: str = ""

    @staticmethod
    def digest(stdout: str, stderr: str) -> str:
        return hashlib.sha256(stdout.encode() + b"\0" + stderr.encode()).hexdigest()
