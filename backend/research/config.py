"""Trusted Stage 11 configuration (configuration/research.yaml)."""
from __future__ import annotations

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator

from backend.settings import REPO_ROOT

from .contracts import ExecutionBudget, NetworkMode, ResearchMode


class ProviderConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    id: str = Field(pattern=r"^[a-z][a-z0-9_-]{1,40}$")
    kind: Literal["searxng", "brave"]
    endpoint: str = Field(min_length=8, max_length=2048)
    max_results: int = Field(10, ge=1, le=50)
    enabled: bool = True

    @field_validator("endpoint")
    @classmethod
    def _https(cls, v):
        if not v.startswith(("https://", "http://")):
            raise ValueError("provider endpoint must be an http(s) URL")
        return v


class EvidenceSelection(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    max_passages_per_task: int = Field(12, ge=1, le=100)
    max_passage_chars: int = Field(1200, ge=100, le=20000)


class ResearchConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    network_mode: NetworkMode = NetworkMode.ONLINE
    providers: list[ProviderConfig] = Field(default_factory=list)
    official_domain_suffixes: list[str] = Field(default_factory=list)
    max_active_subtasks: int = Field(4, ge=1, le=4)          # Focus-4: never above four
    evidence_selection: EvidenceSelection = Field(default_factory=EvidenceSelection)
    modes: dict[ResearchMode, ExecutionBudget]

    @field_validator("modes")
    @classmethod
    def _all_modes(cls, v):
        missing = set(ResearchMode) - set(v)
        if missing:
            raise ValueError(f"modes missing: {sorted(m.value for m in missing)}")
        return v

    def ceiling(self, mode: ResearchMode) -> ExecutionBudget:
        return self.modes[mode]

    def provider(self, provider_id: str) -> ProviderConfig | None:
        return next((p for p in self.providers if p.id == provider_id), None)


def load_research_config(path: Path | None = None) -> ResearchConfig:
    p = path or REPO_ROOT / "configuration" / "research.yaml"
    return ResearchConfig.model_validate(yaml.safe_load(p.read_text(encoding="utf-8")) or {})
