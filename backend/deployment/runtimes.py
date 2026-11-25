"""Deterministic model-runtime selection (Stage 01B §6). Foundation only — inference is Stage 03.

Never selects an external provider, never downloads a model, never substitutes a different model."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class RuntimeChoice:
    status: str                  # SELECTED | MODEL_NOT_FOUND | MODEL_INCOMPATIBLE | RUNTIME_UNAVAILABLE
    model_id: str
    runtime: str | None = None
    model_path: str | None = None
    reason: str = ""


def available_runtimes(env: dict) -> set[str]:
    rt = env.get("runtimes", {})
    out = set()
    if (rt.get("llamacpp") or {}).get("binary"):
        out.add("llamacpp")
    if (rt.get("ollama") or {}).get("binary"):
        out.add("ollama")
    if (rt.get("vllm") or {}).get("python_package"):
        out.add("vllm")
    for e in (rt.get("endpoints") or {}).values():
        if e.get("reachable") and e.get("kind") in ("llamacpp", "ollama", "vllm"):
            out.add(e["kind"])
    return out


def select(profile: str, model_id: str, models: list, env: dict, preference: list[str]) -> RuntimeChoice:
    from .models import find
    m = find(models, model_id)
    if m is None:
        return RuntimeChoice("MODEL_NOT_FOUND", model_id, reason="no model with this identifier in the discovered "
                             "model locations; nothing will be downloaded")
    if m.status != "AVAILABLE":
        return RuntimeChoice("MODEL_INCOMPATIBLE", model_id, model_path=m.path, reason=f"model file is {m.status}")
    gpu = bool(env.get("gpu", {}).get("present"))
    allowed = list(m.compatible_runtimes)
    if "vllm" in allowed and not gpu:
        allowed.remove("vllm")
    if profile == "LOCAL_CPU":
        allowed = [r for r in allowed if r in ("llamacpp", "ollama")]
    if not allowed:
        return RuntimeChoice("MODEL_INCOMPATIBLE", model_id, model_path=m.path,
                             reason=f"{m.format} model has no runtime usable under {profile}"
                                    + ("" if gpu else " (no NVIDIA GPU)"))
    installed = available_runtimes(env)
    for r in preference:
        if r in allowed and r in installed:
            return RuntimeChoice("SELECTED", model_id, r, m.path, f"{m.format} via {r} (profile {profile})")
    return RuntimeChoice("RUNTIME_UNAVAILABLE", model_id, model_path=m.path,
                         reason=f"compatible runtimes {allowed} are not installed; install one (no external fallback)")
