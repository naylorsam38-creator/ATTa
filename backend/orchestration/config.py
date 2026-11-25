"""Trusted controller configuration (configuration/controller.yaml)."""
from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field

from backend.settings import REPO_ROOT


class Limits(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    max_active_tasks_per_objective: int = Field(4, ge=1, le=16)
    max_tools_per_task: int = Field(4, ge=1, le=16)
    max_actions_per_task: int = Field(30, ge=1)
    max_retries_per_step: int = Field(2, ge=0)
    max_failed_steps_per_task: int = Field(3, ge=1)
    max_concurrent_tools_per_task: int = Field(2, ge=1)
    max_task_duration_s: float = Field(1800, gt=0)
    max_proposal_corrections: int = Field(2, ge=0)
    max_repairs_per_task: int = Field(2, ge=0)
    no_progress_window: int = Field(3, ge=2)


class ControllerConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    limits: Limits = Field(default_factory=Limits)
    default_resource_budget: dict[str, float] = Field(default_factory=dict)
    tool_limit_exceptions: dict[str, int] = Field(default_factory=dict)
    default_model_context_tokens: int = 8192
    operator_public_key: str = "configuration/controller_operator_ed25519.pub"
    protected_path_prefixes: list[str] = Field(default_factory=list)


def load_controller_config(path: Path | None = None) -> ControllerConfig:
    p = path or REPO_ROOT / "configuration" / "controller.yaml"
    return ControllerConfig.model_validate(yaml.safe_load(p.read_text(encoding="utf-8")) or {})
