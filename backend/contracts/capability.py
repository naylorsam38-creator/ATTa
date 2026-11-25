"""Capability availability contracts (the registry's public view of each capability)."""
from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field


class RiskClass(str, Enum):
    LOW = "low"            # read-only, no side effects outside the platform
    MEDIUM = "medium"      # writes platform data
    HIGH = "high"          # external side effects (network, files, messages)
    CRITICAL = "critical"  # irreversible / financial / device control — always needs explicit approval


class AvailabilityState(str, Enum):
    AVAILABLE = "available"
    DISABLED = "disabled"
    INVALID = "invalid"
    DEPENDENCY_MISSING = "dependency_missing"
    UNHEALTHY = "unhealthy"
    REJECTED_DUPLICATE = "rejected_duplicate"


class CapabilityAvailability(BaseModel):
    id: str
    version: str | None = None
    state: AvailabilityState
    executable: bool
    source: str
    requirement: str | None = None
    risk: RiskClass | None = None
    problems: list[str] = Field(default_factory=list)
