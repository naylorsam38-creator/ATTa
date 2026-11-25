"""Model gateway boundary (CAP-02 foundation).

Stage 01 scope: the configured model endpoints (local llama.cpp / Ollama, GPU vLLM, LiteLLM-routed providers) are
enumerated and their health is probed for real. Request routing through LiteLLM is Stage 03 (BLOCKERS.md B-003).
No model is downloaded and no paid endpoint is called: endpoints requiring a key report 'not configured' unless the
named environment variable is present and security.allow_paid_infrastructure is true.
"""
from __future__ import annotations

import os
from dataclasses import dataclass

from backend.contracts import DependencyHealth, HealthStatus
from backend.service_manager.probes import probe_model_endpoint
from backend.settings import Settings

LOCAL_KINDS = frozenset({"ollama", "llamacpp", "vllm", "openai_compatible", "litellm"})


@dataclass(frozen=True)
class EndpointInfo:
    name: str
    kind: str
    base_url: str
    enabled: bool
    local: bool
    key_configured: bool


def endpoints(settings: Settings) -> list[EndpointInfo]:
    return [EndpointInfo(e.name, e.kind, e.base_url, e.enabled, e.kind in LOCAL_KINDS,
                         bool(e.api_key_env and os.environ.get(e.api_key_env)))
            for e in settings.models.endpoints]


def endpoint_health(settings: Settings) -> list[DependencyHealth]:
    out = []
    for e in settings.models.endpoints:
        if not e.enabled:
            continue
        if e.kind not in LOCAL_KINDS and not settings.security.allow_paid_infrastructure:
            out.append(DependencyHealth(name=f"model:{e.name}", essential=False, status=HealthStatus.UNKNOWN,
                                        detail="remote paid endpoint not probed: allow_paid_infrastructure is false"))
            continue
        out.append(probe_model_endpoint(e.name, e.base_url, e.health_path))
    return out
