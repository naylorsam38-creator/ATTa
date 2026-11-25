"""Deployment profiles: deterministic AUTO selection, compatibility validation and resource derivation (§2, §18)."""
from __future__ import annotations

from dataclasses import dataclass, field

PROFILES = ("LOCAL_CPU", "LOCAL_NVIDIA", "H100", "TEST_ISOLATED", "AIR_GAPPED")
H100_MIN_VRAM_MB = 79 * 1024          # "H100 80 GB" reports ~81559 MiB
MIN_LOCAL_NVIDIA_VRAM_MB = 4 * 1024


@dataclass
class ProfileDecision:
    requested: str
    selected: str | None
    compatible: bool
    reasons: list[str] = field(default_factory=list)
    resources: dict = field(default_factory=dict)


def _is_h100(g: dict) -> bool:
    return "H100" in g["name"].upper() and g["vram_mb"] >= H100_MIN_VRAM_MB


def check(profile: str, env: dict) -> list[str]:
    """Problems that make `profile` incompatible with the discovered environment (empty = compatible)."""
    gpu = env.get("gpu") or {}
    gpus = gpu.get("gpus") or []
    problems = []
    if profile == "H100":
        if not gpu.get("present"):
            problems.append(f"H100 profile requires an NVIDIA GPU: {gpu.get('reason') or 'none detected'}")
        elif not any(_is_h100(g) for g in gpus):
            problems.append("no NVIDIA H100 with >= 79 GB VRAM detected: " + ", ".join(
                f"{g['name']} ({g['vram_mb']} MB)" for g in gpus))
        if gpu.get("present") and not gpu.get("cuda"):
            problems.append("CUDA driver version not reported by nvidia-smi")
        if not (env.get("runtimes", {}).get("vllm", {}).get("python_package")):
            problems.append("vLLM runtime not installed (required primary runtime for H100)")
        if not str(env.get("operating_system", {}).get("system", "")).startswith("Linux"):
            problems.append("H100 profile is supported on Linux only")
    elif profile == "LOCAL_NVIDIA":
        if not gpu.get("present"):
            problems.append(f"LOCAL_NVIDIA requires an NVIDIA GPU: {gpu.get('reason') or 'none detected'}")
        elif max(g["vram_mb"] for g in gpus) < MIN_LOCAL_NVIDIA_VRAM_MB:
            problems.append("NVIDIA GPU has less than 4 GB VRAM")
    elif profile == "AIR_GAPPED":
        iso = env.get("isolation", {})
        if not iso.get("network_namespace") and env.get("external_network", {}).get("available"):
            problems.append("external network is reachable and no network isolation is available: the machine is not "
                            "air-gapped and cannot be isolated")
    elif profile not in PROFILES:
        problems.append(f"unknown profile {profile}")
    return problems


def auto_select(env: dict) -> str:
    """Deterministic: H100 if a compatible H100 environment exists, else LOCAL_NVIDIA if a usable NVIDIA GPU, else
    LOCAL_CPU. AIR_GAPPED/TEST_ISOLATED are never chosen implicitly — they are explicit operating modes."""
    for p in ("H100", "LOCAL_NVIDIA"):
        if not check(p, env):
            return p
    return "LOCAL_CPU"


def resources(profile: str, env: dict, configured_caps: dict | None = None) -> dict:
    """Resource profile. Values only LOWER the trusted controller limits (resolver applies min())."""
    ram = env.get("ram", {}).get("total_mb", 4096)
    cores = env.get("cpu", {}).get("logical_cores") or 2
    vram = max((g["vram_mb"] for g in env.get("gpu", {}).get("gpus", [])), default=0)
    if profile in ("LOCAL_CPU", "TEST_ISOLATED", "AIR_GAPPED"):
        r = {"worker_count": max(1, min(2, cores // 2)), "parallel_tasks": 2 if ram < 8192 else 4,
             "worker_memory_mb": max(512, min(1024, ram // 8)), "model_context_tokens": 4096 if ram < 16384 else 8192,
             "max_concurrent_tools_per_task": 1, "background_jobs": 1}
    elif profile == "LOCAL_NVIDIA":
        r = {"worker_count": max(2, min(4, cores // 2)), "parallel_tasks": 4,
             "worker_memory_mb": max(1024, min(2048, ram // 8)),
             "model_context_tokens": 4096 if vram < 8192 else (8192 if vram < 16384 else 16384),
             "max_concurrent_tools_per_task": 2, "background_jobs": 2, "gpu_vram_mb": vram}
    else:  # H100
        r = {"worker_count": min(8, cores), "parallel_tasks": 4, "worker_memory_mb": 4096,
             "model_context_tokens": 32768, "max_concurrent_tools_per_task": 2, "background_jobs": 4,
             "gpu_vram_mb": vram, "gpu_count": len(env.get("gpu", {}).get("gpus", []))}
    caps = {"max_active_tasks_per_objective": r["parallel_tasks"],
            "max_concurrent_tools_per_task": r["max_concurrent_tools_per_task"]}
    for k, v in (configured_caps or {}).items():
        caps[k] = min(caps.get(k, v), v)
    r["controller_caps"] = caps
    return r


def decide(requested: str, env: dict, configured_caps: dict | None = None) -> ProfileDecision:
    requested = (requested or "AUTO").upper()
    selected = auto_select(env) if requested == "AUTO" else requested
    problems = check(selected, env)
    return ProfileDecision(requested, selected if not problems else None, not problems, problems,
                           resources(selected, env, configured_caps) if not problems else {})
