"""Dependency lock validation and drift detection (Stage 01B §11)."""
from __future__ import annotations

import importlib.metadata as md
import json
import re
from pathlib import Path

from backend.settings.loader import REPO_ROOT

LOCK_LINE = re.compile(r"^([A-Za-z0-9_.\-]+)==([^\s;\\]+)")


def _norm(n: str) -> str:
    return re.sub(r"[-_.]+", "-", n).lower()


def python_lock(path: Path) -> dict[str, str]:
    """Locked name -> version, evaluating environment markers (e.g. win32-only packages) for THIS machine."""
    from packaging.markers import Marker
    out = {}
    for line in path.read_text().splitlines():
        m = LOCK_LINE.match(line.strip())
        if not m:
            continue
        marker = line.split(";", 1)[1].rstrip(" \\").strip() if ";" in line else ""
        if marker and not Marker(marker).evaluate():
            continue
        out[_norm(m.group(1))] = m.group(2)
    return out


def python_drift(lock: Path | None = None) -> dict:
    lock = lock or REPO_ROOT / "configuration" / "locks" / "backend-core.lock.txt"
    want = python_lock(lock)
    installed = {_norm(d.metadata["Name"]): d.version for d in md.distributions()}
    missing = sorted(n for n in want if n not in installed)
    drift = {n: {"locked": v, "installed": installed[n]} for n, v in want.items() if n in installed and installed[n] != v}
    unlocked_risky = sorted(n for n in installed if n not in want and n not in ("pip", "setuptools", "atta-platform"))
    return {"lock": str(lock.relative_to(REPO_ROOT)) if REPO_ROOT in lock.resolve().parents else str(lock), "locked": len(want), "missing": missing, "drift": drift,
            "extra_installed": unlocked_risky, "ok": not missing and not drift}


def node_drift(root: Path | None = None) -> dict:
    root = root or REPO_ROOT
    lock = json.loads((root / "package-lock.json").read_text())
    nm = root / "node_modules"
    if not nm.is_dir():
        return {"installed": False, "ok": False, "missing": ["node_modules (run INSTALL)"], "drift": {}}
    drift, missing = {}, []
    for key, meta in lock.get("packages", {}).items():
        if not key.startswith("node_modules/") or meta.get("link") or meta.get("optional") or meta.get("dev") is None and False:
            continue
        pj = root / key / "package.json"
        if not pj.is_file():
            if not meta.get("optional") and not meta.get("peer"):
                missing.append(key)
            continue
        v = json.loads(pj.read_text()).get("version")
        if meta.get("version") and v != meta["version"]:
            drift[key] = {"locked": meta["version"], "installed": v}
    return {"installed": True, "ok": not drift and not missing, "missing": missing[:50], "missing_count": len(missing),
            "drift": drift}


def unbounded_specs() -> list[str]:
    """Production manifests must not use unbounded 'latest'-style specifiers."""
    problems = []
    pkg = json.loads((REPO_ROOT / "package.json").read_text())
    for ws in ["."] + pkg.get("workspaces", []) + ["apps/mobile"]:
        pj = json.loads((REPO_ROOT / ws / "package.json").read_text())
        for section in ("dependencies", "devDependencies"):
            for name, spec in (pj.get(section) or {}).items():
                if spec in ("*", "latest", "") or spec.startswith(("^", "~", ">")):
                    problems.append(f"{ws}/package.json {name}@{spec}")
    import yaml
    compose = yaml.safe_load((REPO_ROOT / "infrastructure" / "docker" / "compose.yaml").read_text())
    for name, svc in compose["services"].items():
        img = svc.get("image")
        if img and (":" not in img or img.endswith(":latest")):
            problems.append(f"compose service {name} image {img} is unpinned")
    return problems


def report() -> dict:
    return {"python": python_drift(), "node": node_drift(), "unbounded": unbounded_specs()}
