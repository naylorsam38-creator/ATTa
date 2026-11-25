"""Read-only machine discovery (Stage 01B §3). Nothing here installs, writes or changes the machine — it only
reads OS facts, runs version/query commands and attempts connections. Results feed the environment manifest."""
from __future__ import annotations

import json
import os
import platform
import re
import shutil
import socket
import subprocess
import sys
from pathlib import Path
from typing import Any

import psutil

TIMEOUT = 5


def _run(cmd: list[str], timeout: float = TIMEOUT) -> tuple[int | None, str]:
    exe = shutil.which(cmd[0])
    if exe is None:
        return None, ""
    try:
        cp = subprocess.run([exe, *cmd[1:]], capture_output=True, text=True, timeout=timeout,
                            stdin=subprocess.DEVNULL)
        return cp.returncode, (cp.stdout or "") + (cp.stderr or "")
    except (OSError, subprocess.TimeoutExpired):
        return None, ""


def _version(cmd: list[str]) -> str | None:
    code, out = _run(cmd)
    if code is None:
        return None
    m = re.search(r"\d+\.\d+(\.\d+)?", out)
    return m.group(0) if m else out.strip()[:80] or None


def cpu_info() -> dict:
    model = platform.processor() or ""
    if sys.platform.startswith("linux"):
        try:
            for line in Path("/proc/cpuinfo").read_text().splitlines():
                if line.startswith("model name"):
                    model = line.split(":", 1)[1].strip()
                    break
        except OSError:
            pass
    return {"model": model or "unknown", "architecture": platform.machine(),
            "physical_cores": psutil.cpu_count(logical=False), "logical_cores": psutil.cpu_count(logical=True)}


def memory_info() -> dict:
    vm = psutil.virtual_memory()
    return {"total_mb": vm.total // 2**20, "available_mb": vm.available // 2**20}


def storage_info() -> list[dict]:
    out, seen = [], set()
    for p in psutil.disk_partitions(all=False):
        if p.mountpoint in seen or p.fstype in ("squashfs", "tmpfs", "overlay") and p.mountpoint != "/":
            continue
        seen.add(p.mountpoint)
        try:
            u = psutil.disk_usage(p.mountpoint)
        except (PermissionError, OSError):
            continue
        removable = "removable" in (p.opts or "") or p.mountpoint.startswith(("/media/", "/run/media/", "/mnt/usb"))
        out.append({"mountpoint": p.mountpoint, "device": p.device, "fstype": p.fstype, "removable": removable,
                    "total_mb": u.total // 2**20, "free_mb": u.free // 2**20})
    return out


def nvidia_gpus() -> dict:
    """Query NVIDIA GPUs via nvidia-smi (the only source trusted for H100 detection)."""
    code, out = _run(["nvidia-smi", "--query-gpu=name,memory.total,driver_version,uuid",
                      "--format=csv,noheader,nounits"])
    if code is None:
        return {"present": False, "reason": "nvidia-smi not found", "gpus": [], "driver": None, "cuda": None}
    if code != 0:
        return {"present": False, "reason": f"nvidia-smi failed: {out.strip()[:200]}", "gpus": [], "driver": None,
                "cuda": None}
    gpus = []
    for line in out.strip().splitlines():
        parts = [x.strip() for x in line.split(",")]
        if len(parts) >= 3:
            gpus.append({"name": parts[0], "vram_mb": int(float(parts[1])), "driver": parts[2],
                         "id": f"gpu{len(gpus)}"})   # UUIDs are not recorded (stable identifiers stay local)
    _, full = _run(["nvidia-smi"])
    m = re.search(r"CUDA Version:\s*([\d.]+)", full)
    return {"present": bool(gpus), "gpus": gpus, "driver": gpus[0]["driver"] if gpus else None,
            "cuda": m.group(1) if m else None, "reason": None if gpus else "no GPU listed"}


def docker_info() -> dict:
    v = _version(["docker", "--version"])
    if v is None:
        return {"cli": None, "daemon": False}
    code, _ = _run(["docker", "info", "--format", "{{.ServerVersion}}"])
    return {"cli": v, "daemon": code == 0}


def wsl_info() -> dict:
    if os.name != "nt":
        return {"applicable": False}
    code, out = _run(["wsl", "--status"])
    return {"applicable": True, "available": code == 0, "detail": out.strip()[:200]}


def tcp_open(host: str, port: int, timeout: float = 0.5) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def external_network(probe: str | None, timeout: float = 2.0) -> dict:
    """Optional, configurable reachability probe (host:port). None = not probed (e.g. AIR_GAPPED)."""
    if not probe:
        return {"probed": False, "available": None}
    host, _, port = probe.rpartition(":")
    try:
        with socket.create_connection((host, int(port)), timeout=timeout):
            return {"probed": True, "available": True, "target": probe}
    except OSError as exc:
        return {"probed": True, "available": False, "target": probe, "error": type(exc).__name__}


def runtimes(settings) -> dict:
    """Model runtimes present on this machine: binaries, Python packages and reachable endpoints."""
    import importlib.util
    llama = next((b for b in ("llama-server", "llama-cli", "llama.cpp", "server") if shutil.which(b) and b != "server"), None)
    endpoints = {e.name: {"kind": e.kind, "base_url": e.base_url, "enabled": e.enabled} for e in settings.models.endpoints}
    out = {
        "llamacpp": {"binary": shutil.which(llama) if llama else None, "version": _version([llama, "--version"]) if llama else None},
        "ollama": {"binary": shutil.which("ollama"), "version": _version(["ollama", "--version"])},
        "vllm": {"python_package": importlib.util.find_spec("vllm") is not None},
        "endpoints": {},
    }
    from backend.service_manager.probes import probe_model_endpoint
    for e in settings.models.endpoints:
        if e.kind in ("ollama", "llamacpp", "vllm", "openai_compatible", "litellm"):
            h = probe_model_endpoint(e.name, e.base_url, e.health_path, timeout=1.0)
            endpoints[e.name]["reachable"] = h.status.value == "healthy"
    out["endpoints"] = endpoints
    return out


def services(settings) -> dict:
    from backend.service_manager.probes import probe_service
    res = {}
    for name, spec in settings.services.items():
        if spec.mode == "disabled" or name in ("api", "controller"):
            continue
        h = probe_service(name, spec.essential, spec.health, settings)
        res[name] = {"essential": spec.essential, "mode": spec.mode, "healthy": h.status.value == "healthy",
                     "detail": (h.detail or "")[:200]}
    res["pgvector_package"] = {"installed": _pgvector_installed()}
    return res


def _pgvector_installed() -> bool:
    code, out = _run(["pg_config", "--sharedir"])
    if code == 0 and out.strip():
        return (Path(out.strip()) / "extension" / "vector.control").is_file()
    return any(Path(p).is_file() for p in ("/usr/share/postgresql/16/extension/vector.control",))


def ports(settings) -> dict:
    wanted = {"api": settings.api.port, "redis": settings.redis.port, "postgres": settings.database.port,
              "web": 3000}
    return {name: {"port": p, "in_use": tcp_open("127.0.0.1", p)} for name, p in wanted.items()}


def isolation_capabilities() -> dict:
    caps = {"platform": sys.platform, "is_root": hasattr(os, "geteuid") and os.geteuid() == 0}
    if sys.platform.startswith("linux"):
        caps["network_namespace"] = _netns_works()
        caps["cgroup_v1_memory"] = os.access("/sys/fs/cgroup/memory", os.W_OK)
        caps["cgroup_v1_pids"] = os.access("/sys/fs/cgroup/pids", os.W_OK)
        caps["cgroup_v2"] = Path("/sys/fs/cgroup/cgroup.controllers").is_file()
        caps["subreaper"] = True
        caps["runuser"] = shutil.which("runuser") is not None
    elif os.name == "nt":
        caps["job_objects"] = True        # implemented in capability_runtime (untested on Windows: BLOCKERS)
        caps["network_namespace"] = False
    return caps


def _netns_works() -> bool:
    exe = shutil.which("unshare")
    if not exe:
        return False
    try:
        return subprocess.run([exe, "-n", "true"], capture_output=True, timeout=5).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def discover(settings, *, network_probe: str | None = None) -> dict[str, Any]:
    """Read-only snapshot of this machine. Hostname is reduced to a non-reversible identifier."""
    import hashlib
    uname = platform.uname()
    return {
        "operating_system": {"system": uname.system, "release": uname.release, "version": uname.version[:120],
                             "wsl": wsl_info()},
        "architecture": platform.machine(),
        "host_id": hashlib.sha256(socket.gethostname().encode()).hexdigest()[:16],
        "cpu": cpu_info(),
        "ram": memory_info(),
        "storage": storage_info(),
        "gpu": nvidia_gpus(),
        "docker": docker_info(),
        "tools": {"python": platform.python_version(), "node": _version(["node", "--version"]),
                  "git": _version(["git", "--version"]), "npm": _version(["npm", "--version"]),
                  "redis_server": _version(["redis-server", "--version"]), "psql": _version(["psql", "--version"]),
                  "initdb": shutil.which("initdb") or _pg_bin("initdb"), "unshare": shutil.which("unshare")},
        "services": services(settings),
        "runtimes": runtimes(settings),
        "ports": ports(settings),
        "network_interfaces": sorted(psutil.net_if_addrs().keys()),
        "external_network": external_network(network_probe),
        "isolation": isolation_capabilities(),
    }


def _pg_bin(name: str) -> str | None:
    for base in sorted(Path("/usr/lib/postgresql").glob("*/bin"), reverse=True) if Path("/usr/lib/postgresql").is_dir() else []:
        if (base / name).is_file():
            return str(base / name)
    return None


if __name__ == "__main__":
    from backend.settings import load_settings
    print(json.dumps(discover(load_settings()), indent=2, default=str))
