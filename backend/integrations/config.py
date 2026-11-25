"""Stage 12 configuration: configuration/integrations.yaml -> integrations.local.yaml -> ATTA_INTEGRATIONS__* env.

Kept separate from backend/settings (whose strict schema Stage 01B owns) so the sidecar merges without conflict;
STAGE_12_MERGE_MANIFEST.md describes folding it into the Stage 01B hierarchy."""
from __future__ import annotations

import os
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field

from backend.settings import REPO_ROOT

from .types import ConnectivityMode


class IntegrationsConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    source_directories: list[str] = Field(default_factory=lambda: ["integrations/available"])
    install_root: str = "runtime/integrations"
    connectivity: ConnectivityMode = ConnectivityMode.ONLINE
    allowed_network_hosts: list[str] = Field(default_factory=list)
    allowed_mcp_executables: list[str] = Field(default_factory=list)
    mcp_definitions: str | None = None
    max_result_bytes: int = Field(262144, ge=1024, le=16 * 1024 * 1024)
    health_max_age_s: float = Field(30, ge=0)
    mcp_request_timeout_s: float = Field(30, gt=0, le=600)
    mcp_startup_timeout_s: float = Field(10, gt=0, le=60)
    proposed_permissions: str | None = None

    def path(self, value: str | None) -> Path | None:
        if value is None:
            return None
        p = Path(value)
        return p if p.is_absolute() else REPO_ROOT / p


_LIST_KEYS = {"source_directories", "allowed_network_hosts", "allowed_mcp_executables"}


def load_integrations_config(path: Path | None = None, env: dict | None = None, **overrides) -> IntegrationsConfig:
    env = os.environ if env is None else env
    p = path or REPO_ROOT / "configuration" / "integrations.yaml"
    data = yaml.safe_load(p.read_text(encoding="utf-8")) if p.is_file() else {}
    local = p.with_name("integrations.local.yaml")
    if path is None and local.is_file():
        data.update(yaml.safe_load(local.read_text(encoding="utf-8")) or {})
    for k, v in env.items():
        if k.startswith("ATTA_INTEGRATIONS__"):
            key = k[len("ATTA_INTEGRATIONS__"):].lower()
            data[key] = [s for s in v.split(",") if s] if key in _LIST_KEYS else v
    data.update(overrides)
    return IntegrationsConfig.model_validate(data or {})
