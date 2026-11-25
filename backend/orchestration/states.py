"""Persistent task state machine (Stage 01A §14): the only permitted transitions."""
from __future__ import annotations

TRANSITIONS: dict[str, frozenset[str]] = {
    "CREATED": frozenset({"VALIDATING", "BLOCKED", "CANCELLED"}),
    "VALIDATING": frozenset({"PLANNING", "BLOCKED", "FAILED"}),
    "PLANNING": frozenset({"READY", "WAITING_FOR_DEPENDENCY", "AWAITING_APPROVAL", "BLOCKED", "CANCELLED"}),
    "WAITING_FOR_DEPENDENCY": frozenset({"READY", "BLOCKED", "CANCELLED"}),
    "AWAITING_APPROVAL": frozenset({"READY", "BLOCKED", "CANCELLED"}),
    "READY": frozenset({"QUEUED", "WAITING_FOR_DEPENDENCY", "BLOCKED", "CANCELLED"}),
    "QUEUED": frozenset({"RUNNING", "WAITING_FOR_DEPENDENCY", "CANCELLED", "RECOVERING"}),
    "RUNNING": frozenset({"VERIFYING", "RETRYING", "FAILED", "BLOCKED", "CANCELLED", "RECOVERING"}),
    "RETRYING": frozenset({"RUNNING", "FAILED", "BLOCKED", "CANCELLED", "RECOVERING"}),
    "VERIFYING": frozenset({"COMPLETED", "RETRYING", "FAILED", "BLOCKED", "RECOVERING"}),
    "RECOVERING": frozenset({"QUEUED", "RUNNING", "VERIFYING", "BLOCKED", "FAILED"}),
    "BLOCKED": frozenset({"PLANNING", "CANCELLED"}),   # PLANNING only with a NEW contract version
    "CANCELLED": frozenset({"QUEUED"}),                 # only via an operator-authorised new attempt
    "COMPLETED": frozenset(),                           # never reopened
    "FAILED": frozenset(),
}
ACTIVE_STATES = frozenset({"QUEUED", "RUNNING", "RETRYING", "VERIFYING", "RECOVERING"})
TERMINAL_STATES = frozenset({"COMPLETED", "FAILED"})


class InvalidTaskTransition(ValueError):
    pass


def check_transition(current: str, target: str) -> None:
    if target not in TRANSITIONS.get(current, frozenset()):
        raise InvalidTaskTransition(f"task cannot move from {current} to {target}")
