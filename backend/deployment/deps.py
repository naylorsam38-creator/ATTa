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


# Source trees whose imports must be satisfiable from the locks (legacy ATTa uses the standard library only and is
# covered as well, so a new third-party import anywhere is caught).
SOURCE_DIRS = ["backend", "capabilities", "scripts", "02-front-door", "04-deployment"]


def _locked_names(lock: Path) -> set[str]:
    out = set()
    for line in lock.read_text().splitlines():
        m = LOCK_LINE.match(line.strip())
        if m:
            out.add(_norm(m.group(1)))
    return out


IMPORT_ALIASES = {"yaml": "pyyaml", "PIL": "pillow", "dateutil": "python-dateutil", "dotenv": "python-dotenv",
                  "jwt": "pyjwt", "google": "protobuf"}


def import_coverage() -> dict:
    """Every top-level third-party module imported by the source must map to a distribution pinned in a lock.
    Core imports must be in backend-core.lock.txt; optional (try/except ImportError, extras) in backend-full."""
    import ast
    import sys
    dist_of = md.packages_distributions()
    local = {p.name for p in REPO_ROOT.iterdir() if p.is_dir()} | {"backend", "capabilities", "handler", "s01_helpers"}
    core = _locked_names(REPO_ROOT / "configuration/locks/backend-core.lock.txt")
    full = _locked_names(REPO_ROOT / "configuration/locks/backend-full.lock.txt")
    for d in SOURCE_DIRS:                                   # in-repository modules (legacy code adds its dirs to sys.path)
        base = REPO_ROOT / d
        local |= {q.stem for q in base.rglob("*.py")} | {q.parent.name for q in base.rglob("*.py")} \
            if base.is_dir() else set()
    import yaml
    allowed = (yaml.safe_load((REPO_ROOT / "configuration/locks/system-dependencies.yaml").read_text())
               .get("unlocked_optional_imports") or {})
    seen: dict[str, set[str]] = {}
    for d in SOURCE_DIRS:
        base = REPO_ROOT / d
        for f in base.rglob("*.py") if base.is_dir() else []:
            try:
                tree = ast.parse(f.read_text(encoding="utf-8", errors="replace"))
            except SyntaxError:
                continue
            local_mods = {q.stem for q in f.parent.glob("*.py")} | {q.name for q in f.parent.iterdir() if q.is_dir()}
            for node in ast.walk(tree):
                names = []
                if isinstance(node, ast.Import):
                    names = [a.name for a in node.names]
                elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                    names = [node.module]
                for n in names:
                    top = n.split(".")[0]
                    if top in sys.stdlib_module_names or top in local or top in local_mods or top == "__future__":
                        continue
                    rel = f.relative_to(REPO_ROOT) if REPO_ROOT in f.resolve().parents else f
                    seen.setdefault(top, set()).add(str(rel))
    rows, missing, optional_only = {}, [], []
    for top, files in sorted(seen.items()):
        # Installed metadata when available; otherwise the import name itself (or a known alias) must be a locked
        # distribution — optional extras are usually NOT installed on a core-only machine.
        dists = {_norm(x) for x in dist_of.get(top, [])} | {_norm(IMPORT_ALIASES.get(top, top))}
        where = "core" if dists & core else ("full" if dists & full else None)
        rows[top] = {"distributions": sorted(dists), "lock": where, "files": sorted(files)[:5]}
        if where is None and top in allowed:
            rows[top]["lock"] = "unlocked-optional"
            rows[top]["reason"] = allowed[top]
        elif where is None:
            missing.append(top)
        elif where == "full":
            optional_only.append(top)
    return {"modules": rows, "missing": missing, "full_lock_only": optional_only,
            "unlocked_optional": sorted(t for t in rows if rows[t]["lock"] == "unlocked-optional"), "ok": not missing}


def report() -> dict:
    return {"python": python_drift(), "node": node_drift(), "unbounded": unbounded_specs(),
            "imports": import_coverage()}
