"""ops.system_status — real host/runtime facts for operational management (CAP-40)."""
from __future__ import annotations

import os
import platform
import shutil
from datetime import datetime, timezone
from pathlib import Path

import psutil

_ROOT = Path(__file__).resolve().parents[2]


def execute(payload: dict, context) -> dict:
    target = Path(payload.get("path") or _ROOT)
    usage = shutil.disk_usage(target)
    return {
        "platform": f"{platform.system()} {platform.release()} ({platform.machine()})",
        "python": platform.python_version(),
        "cpu_count": os.cpu_count() or 1,
        "memory_total_mb": int(psutil.virtual_memory().total // (1024 * 1024)),
        "disk_free_mb": int(usage.free // (1024 * 1024)),
        "disk_total_mb": int(usage.total // (1024 * 1024)),
        "checked_at": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
    }


def healthcheck() -> dict:
    usage = shutil.disk_usage(_ROOT)
    return {"healthy": usage.total > 0 and psutil.virtual_memory().total > 0, "root": str(_ROOT)}


def verify(payload: dict, output: dict) -> dict:
    problems = []
    if output["disk_free_mb"] > output["disk_total_mb"]:
        problems.append("free disk exceeds total disk")
    if output["cpu_count"] != (os.cpu_count() or 1):
        problems.append("cpu_count does not match the host")
    return {"verified": not problems, "problems": problems}
