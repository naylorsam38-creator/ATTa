"""Offline dependency bundle (Stage 01B §12): wheels, npm cache, migrations, locks and a SHA-256 manifest.
Never includes private keys. Model weights are only included when the operator explicitly passes --models."""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from backend.contracts import iso_utc
from backend.settings.loader import REPO_ROOT

LOCK = REPO_ROOT / "configuration" / "locks" / "backend-core.lock.txt"
EXCLUDE_MARKERS = (b"PRIVATE KEY-----",)


def _sha(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def pip_command() -> list[str]:
    """A pip for this exact interpreter version: the running one if it has pip, else a system pip of the same X.Y
    (uv-created virtual environments ship without pip). Wheels are then selected for this interpreter's tags."""
    if subprocess.run([sys.executable, "-m", "pip", "--version"], capture_output=True).returncode == 0:
        return [sys.executable, "-m", "pip"]
    want = f"{sys.version_info.major}.{sys.version_info.minor}"
    for cand in (f"python{want}", "python3"):
        exe = shutil.which(cand)
        if not exe:
            continue
        ver = subprocess.run([exe, "-c", "import sys;print(f'{sys.version_info.major}.{sys.version_info.minor}')"],
                             capture_output=True, text=True).stdout.strip()
        if ver == want and subprocess.run([exe, "-m", "pip", "--version"], capture_output=True).returncode == 0:
            return [exe, "-m", "pip"]
    raise RuntimeError(f"no pip available for Python {want}; install pip for this interpreter to build a bundle")


def create(out: Path, *, include_node: bool = True, models: list[str] | None = None) -> dict:
    out = out.resolve()                         # npm runs in a temp dir; a relative cache path would land there
    out.mkdir(parents=True, exist_ok=True)
    notes = {}
    r = subprocess.run(pip_command() + ["download", "--disable-pip-version-check", "-q", "--require-hashes",
                        "--no-deps", "--only-binary=:all:", "-r", str(LOCK), "-d", str(out / "wheels")],
                       capture_output=True, text=True, timeout=1800)
    if r.returncode != 0:
        raise RuntimeError(f"pip download failed: {r.stderr[-1500:]}")
    if include_node and shutil.which("npm"):
        with tempfile.TemporaryDirectory() as tmp:
            pkg = json.loads((REPO_ROOT / "package.json").read_text())
            for rel in ["package.json", "package-lock.json", ".npmrc"] + [f"{w}/package.json" for w in pkg["workspaces"]]:
                (Path(tmp) / rel).parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(REPO_ROOT / rel, Path(tmp) / rel)
            r = subprocess.run(["npm", "ci", "--ignore-scripts", "--no-audit", "--no-fund", "--loglevel=error",
                                "--cache", str(out / "npm-cache")], cwd=tmp, capture_output=True, text=True,
                               timeout=1800, env={**os.environ, "ELECTRON_SKIP_BINARY_DOWNLOAD": "1"})
            if r.returncode != 0:
                raise RuntimeError(f"npm cache population failed: {r.stderr[-1500:]}")
    for rel in ("database", "configuration/locks"):
        shutil.copytree(REPO_ROOT / rel, out / "assets" / rel, dirs_exist_ok=True)
    notes["container_images"] = "BLOCKED: no Docker daemon to `docker save` pinned images on this machine" if \
        subprocess.run(["docker", "info"], capture_output=True).returncode != 0 else "not included (use --images)"
    if models:
        (out / "models").mkdir(exist_ok=True)
        for m in models:                       # only on explicit operator request
            shutil.copy2(m, out / "models" / Path(m).name)
    files = {}
    for p in sorted(out.rglob("*")):
        if p.is_file() and p.name != "bundle_manifest.json":
            files[p.relative_to(out).as_posix()] = {"sha256": _sha(p), "bytes": p.stat().st_size}
    manifest = {"format": "atta-offline-bundle/1", "created_at": iso_utc(), "python_lock_sha256": _sha(LOCK),
                "node_lock_sha256": _sha(REPO_ROOT / "package-lock.json"), "notes": notes, "files": files,
                "includes_models": bool(models)}
    (out / "bundle_manifest.json").write_text(json.dumps(manifest, indent=2))
    return manifest


def verify(bundle: Path) -> list[str]:
    problems = []
    mpath = bundle / "bundle_manifest.json"
    if not mpath.is_file():
        return ["bundle_manifest.json missing"]
    m = json.loads(mpath.read_text())
    if m.get("python_lock_sha256") != _sha(LOCK):
        problems.append("bundle was built for a different Python lock")
    if m.get("node_lock_sha256") != _sha(REPO_ROOT / "package-lock.json"):
        problems.append("bundle was built for a different Node lock")
    present = {p.relative_to(bundle).as_posix() for p in bundle.rglob("*") if p.is_file()} - {"bundle_manifest.json"}
    for rel, meta in m["files"].items():
        p = bundle / rel
        if not p.is_file():
            problems.append(f"missing: {rel}")
        elif _sha(p) != meta["sha256"]:
            problems.append(f"modified: {rel}")
    for extra in sorted(present - set(m["files"])):
        problems.append(f"unlisted file: {extra}")
    for rel in present:
        p = bundle / rel
        if p.suffix in (".pem", ".key") or (p.stat().st_size < 1 << 20 and any(x in p.read_bytes() for x in EXCLUDE_MARKERS)):
            problems.append(f"private key material in bundle: {rel}")
    return problems


def install_offline(bundle: Path, venv: Path) -> None:
    """Create a fresh virtual environment from the bundle with pip's index disabled (no network needed)."""
    problems = verify(bundle)
    if problems:
        raise RuntimeError("bundle verification failed: " + "; ".join(problems[:5]))
    subprocess.run([sys.executable, "-m", "venv", str(venv)], check=True)
    py = venv / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    r = subprocess.run([str(py), "-m", "pip", "install", "--disable-pip-version-check", "-q", "--no-index",
                        "--find-links", str(bundle / "wheels"), "--require-hashes", "--no-deps", "-r", str(LOCK)],
                       capture_output=True, text=True, timeout=1800)
    if r.returncode != 0:
        raise RuntimeError(f"offline pip install failed: {r.stderr[-1500:]}")
    site = subprocess.run([str(py), "-c", "import sysconfig;print(sysconfig.get_paths()['purelib'])"],
                          capture_output=True, text=True, check=True).stdout.strip()
    Path(site, "atta_platform.pth").write_text(str(REPO_ROOT) + "\n")
    chk = subprocess.run([str(py), "-I", "-c", "import backend.api.app, backend.orchestration.controller, psycopg, "
                          "fastapi; print('offline install OK')"], capture_output=True, text=True, timeout=120)
    if chk.returncode != 0:
        raise RuntimeError(f"offline install import check failed: {chk.stderr[-800:]}")
    if (bundle / "npm-cache").is_dir() and shutil.which("npm"):
        # Node workspace dependencies restored from the bundled cache with npm's network use forbidden (--offline).
        node_dir = venv.parent / "node-offline"
        pkg = json.loads((REPO_ROOT / "package.json").read_text())
        for rel in ["package.json", "package-lock.json", ".npmrc"] + [f"{w}/package.json" for w in pkg["workspaces"]]:
            (node_dir / rel).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(REPO_ROOT / rel, node_dir / rel)
        r = subprocess.run(["npm", "ci", "--offline", "--ignore-scripts", "--no-audit", "--no-fund", "--loglevel=error",
                            "--cache", str((bundle / "npm-cache").resolve())], cwd=node_dir, capture_output=True,
                           text=True, timeout=1800, env={**os.environ, "ELECTRON_SKIP_BINARY_DOWNLOAD": "1"})
        if r.returncode != 0:
            raise RuntimeError(f"offline npm ci failed: {r.stderr[-1500:]}")
        if not (node_dir / "node_modules" / "next" / "package.json").is_file():
            raise RuntimeError("offline npm ci finished but the web framework package is missing")
