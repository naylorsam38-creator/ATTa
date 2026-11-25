"""Execution events: an append-only record of what happened during a task or capability execution."""
from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field

from .identifiers import new_id
from .timeutil import utc_now


class EventType(str, Enum):
    TASK_STATE_CHANGED = "task.state_changed"
    CAPABILITY_REQUESTED = "capability.requested"
    CAPABILITY_DENIED = "capability.denied"
    CAPABILITY_STARTED = "capability.started"
    CAPABILITY_COMPLETED = "capability.completed"
    CAPABILITY_FAILED = "capability.failed"
    SERVICE_STARTED = "service.started"
    SERVICE_FAILED = "service.failed"
    SERVICE_RESTARTED = "service.restarted"
    SERVICE_STOPPED = "service.stopped"
    POLICY_DECISION = "policy.decision"


class ExecutionEvent(BaseModel):
    id: str = Field(default_factory=lambda: new_id("evt"))
    type: EventType
    occurred_at: datetime = Field(default_factory=utc_now)
    correlation_id: str | None = None
    subject: str
    data: dict[str, Any] = Field(default_factory=dict)
