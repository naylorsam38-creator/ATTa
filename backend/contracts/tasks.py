"""Task states and the only permitted transitions between them."""
from __future__ import annotations

from enum import Enum


class TaskState(str, Enum):
    PENDING = "pending"
    QUEUED = "queued"
    AWAITING_APPROVAL = "awaiting_approval"
    RUNNING = "running"
    VERIFYING = "verifying"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    TIMED_OUT = "timed_out"
    BLOCKED = "blocked"


TERMINAL_STATES = frozenset({TaskState.SUCCEEDED, TaskState.FAILED, TaskState.CANCELLED, TaskState.TIMED_OUT})

ALLOWED_TRANSITIONS: dict[TaskState, frozenset[TaskState]] = {
    TaskState.PENDING: frozenset({TaskState.QUEUED, TaskState.BLOCKED, TaskState.CANCELLED}),
    TaskState.BLOCKED: frozenset({TaskState.QUEUED, TaskState.CANCELLED}),
    TaskState.QUEUED: frozenset({TaskState.AWAITING_APPROVAL, TaskState.RUNNING, TaskState.CANCELLED}),
    TaskState.AWAITING_APPROVAL: frozenset({TaskState.RUNNING, TaskState.CANCELLED}),
    TaskState.RUNNING: frozenset({TaskState.VERIFYING, TaskState.FAILED, TaskState.CANCELLED, TaskState.TIMED_OUT}),
    TaskState.VERIFYING: frozenset({TaskState.SUCCEEDED, TaskState.FAILED}),
    TaskState.SUCCEEDED: frozenset(), TaskState.FAILED: frozenset(),
    TaskState.CANCELLED: frozenset(), TaskState.TIMED_OUT: frozenset(),
}


class InvalidTransition(ValueError):
    pass


def assert_transition(current: TaskState, target: TaskState) -> None:
    if target not in ALLOWED_TRANSITIONS[current]:
        raise InvalidTransition(f"task cannot move from {current.value} to {target.value}")
