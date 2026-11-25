"""Task manager: task records that move only through permitted state transitions, each transition emitting an
ExecutionEvent. Stage 01 provides the in-process store; Temporal-backed durable execution is wired in Stage 12/15."""
from __future__ import annotations

import threading
from dataclasses import dataclass, field
from datetime import datetime

from backend.contracts import (EventType, ExecutionEvent, TaskState, assert_transition, get_correlation_id, new_id,
                               utc_now)


@dataclass
class TaskRecord:
    id: str
    kind: str
    owner: str
    state: TaskState = TaskState.PENDING
    created_at: datetime = field(default_factory=utc_now)
    updated_at: datetime = field(default_factory=utc_now)
    events: list[ExecutionEvent] = field(default_factory=list)


class TaskStore:
    def __init__(self):
        self._tasks: dict[str, TaskRecord] = {}
        self._lock = threading.Lock()

    def create(self, kind: str, owner: str) -> TaskRecord:
        t = TaskRecord(id=new_id("tsk"), kind=kind, owner=owner)
        with self._lock:
            self._tasks[t.id] = t
        return t

    def get(self, task_id: str) -> TaskRecord:
        with self._lock:
            return self._tasks[task_id]

    def transition(self, task_id: str, target: TaskState, reason: str = "") -> ExecutionEvent:
        with self._lock:
            t = self._tasks[task_id]
            assert_transition(t.state, target)
            ev = ExecutionEvent(type=EventType.TASK_STATE_CHANGED, subject=t.id, correlation_id=get_correlation_id(),
                                data={"from": t.state.value, "to": target.value, "reason": reason})
            t.state, t.updated_at = target, utc_now()
            t.events.append(ev)
            return ev
