"""Service health contracts. Readiness is derived from real dependency probes, never asserted."""
from __future__ import annotations

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field

from .capability import CapabilityAvailability
from .timeutil import utc_now


class HealthStatus(str, Enum):
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNHEALTHY = "unhealthy"
    UNKNOWN = "unknown"


class DependencyHealth(BaseModel):
    name: str
    essential: bool
    status: HealthStatus
    latency_ms: float | None = None
    detail: str | None = None
    checked_at: datetime = Field(default_factory=utc_now)


class HealthReport(BaseModel):
    status: HealthStatus
    service: str
    version: str
    checked_at: datetime = Field(default_factory=utc_now)


class ReadinessReport(BaseModel):
    ready: bool
    status: HealthStatus
    environment: str
    dependencies: list[DependencyHealth]
    capabilities: dict[str, int] = Field(default_factory=dict)  # counts by availability state (details are protected)
    reasons: list[str] = Field(default_factory=list)
    checked_at: datetime = Field(default_factory=utc_now)

    @classmethod
    def from_dependencies(cls, environment: str, deps: list[DependencyHealth],
                          capabilities: list[CapabilityAvailability] | None = None) -> "ReadinessReport":
        reasons = [f"essential dependency '{d.name}' is {d.status.value}: {d.detail or ''}".strip()
                   for d in deps if d.essential and d.status != HealthStatus.HEALTHY]
        optional_bad = any(not d.essential and d.status != HealthStatus.HEALTHY for d in deps)
        ready = not reasons
        status = HealthStatus.UNHEALTHY if not ready else (HealthStatus.DEGRADED if optional_bad else HealthStatus.HEALTHY)
        counts: dict[str, int] = {}
        for c in capabilities or []:
            counts[c.state.value] = counts.get(c.state.value, 0) + 1
        return cls(ready=ready, status=status, environment=environment, dependencies=deps,
                   capabilities=counts, reasons=reasons)
