"""Offline deployment bundle (Stage 01B §12): everything an offline machine of the same OS/architecture needs.

Components (each recorded in bundle_manifest.json["coverage"] with what was included, or why it is BLOCKED):
  python_wheels      every package of configuration/locks/backend-core.lock.txt AND backend-full.lock.txt (all
                     extras), downloaded with --require-hashes --only-binary=:all: for this interpreter's tags
  python_runtime     a relocatable CPython of the same X.Y (python-build-standalone via uv) + the uv binary, so a
                     target without a suitable Python can still bootstrap
  node               npm cache for the root workspaces lock AND the mobile app lock; coverage is checked entry by
                     entry against package-lock.json integrity hashes
  browsers           Playwright browser builds used by the end-to-end tests and the browser extra
  system_packages    Debian/Ubuntu .deb closure of the system dependency manifest (PostgreSQL 16, pgvector, Redis…)
  container_images   `docker save` of the pinned compose images with their resolved digests (needs a Docker daemon)
  assets             migrations, bootstrap SQL and every lock/manifest file
Never included: private keys (verification fails if any is found), credentials, model weights (only with an
explicit --models list)."""
from __future__ import annotations

import base64
import hashlib
import json
import os
import platform
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import yaml

from backend.contracts import iso_utc
from backend.settings.loader import REPO_ROOT

LOCKS = REPO_ROOT / "configuration" / "locks"
LOCK = LOCKS / "backend-core.lock.txt"
FULL_LOCK = LOCKS / "backend-full.lock.txt"
SYSTEM_MANIFEST = LOCKS / "system-dependencies.yaml"
EXCLUDE_MARKERS = (b"PRIVATE KEY-----",)
FORMAT = "atta-offline-bundle/2"


def _sha(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _run(cmd: list[str], **kw) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, **kw)


def pip_command() -> list[str]:
    """A pip for this exact interpreter version: the running one if it has pip, else a system pip of the same X.Y
    (uv-created virtual environments ship without pip). Wheels are then selected for this interpreter's tags."""
    if _run([sys.executable, "-m", "pip", "--version"]).returncode == 0:
        return [sys.executable, "-m", "pip"]
    want = f"{sys.version_info.major}.{sys.version_info.minor}"
    for cand in (f"python{want}", "python3"):
        exe = shutil.which(cand)
        if not exe:
            continue
        ver = _run([exe, "-c", "import sys;print(f'{sys.version_info.major}.{sys.version_info.minor}')"]).stdout.strip()
        if ver == want and _run([exe, "-m", "pip", "--version"]).returncode == 0:
            return [exe, "-m", "pip"]
    raise RuntimeError(f"no pip available for Python {want}; install pip for this interpreter to build a bundle")


# ------------------------------------------------------------------------------------------------ components
def _python_wheels(out: Path) -> dict:
    cov = {}
    for lock in (LOCK, FULL_LOCK):
        r = _run(pip_command() + ["download", "--disable-pip-version-check", "-q", "--require-hashes", "--no-deps",
                                  "--only-binary=:all:", "-r", str(lock), "-d", str(out / "wheels")], timeout=3600)
        if r.returncode != 0:
            raise RuntimeError(f"pip download failed for {lock.name}: {r.stderr[-1500:]}")
        from .deps import python_lock
        cov[lock.name] = {"packages": len(python_lock(lock)), "sha256": _sha(lock)}
    cov["wheels"] = len(list((out / "wheels").glob("*.whl")))
    cov["interpreter_tags"] = f"cp{sys.version_info.major}{sys.version_info.minor}-{platform.system().lower()}-" \
                              f"{platform.machine().lower()}"
    return cov


def _python_runtime(out: Path) -> dict:
    uv = shutil.which("uv")
    if not uv:
        return {"status": "BLOCKED", "reason": "uv not installed: cannot fetch a relocatable CPython"}
    ver = f"{sys.version_info.major}.{sys.version_info.minor}"
    r = _run([uv, "python", "install", ver, "--install-dir", str(out / "python")], timeout=1800,
             env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"})
    if r.returncode != 0:
        return {"status": "BLOCKED", "reason": f"uv python install failed: {r.stderr[-400:]}"}
    (out / "tools").mkdir(exist_ok=True)
    shutil.copy2(uv, out / "tools" / Path(uv).name)
    for tmp in [p for p in (out / "python").iterdir() if p.name.startswith(".")]:
        shutil.rmtree(tmp, ignore_errors=True) if tmp.is_dir() else tmp.unlink()   # uv work files
    for link in [p for p in (out / "python").iterdir() if p.is_symlink()]:
        link.unlink()                       # minor-version alias links are not needed and are not portable files
    exe = bundled_python(out)
    v = _run([str(exe), "-B", "-c", "import sys;print(sys.version.split()[0])"]).stdout.strip() if exe else None
    return {"status": "INCLUDED", "version": v, "interpreter": str(exe.relative_to(out)) if exe else None,
            "uv": f"tools/{Path(uv).name}"}


def bundled_python(bundle: Path) -> Path | None:
    for d in sorted((bundle / "python").glob("cpython-*")) if (bundle / "python").is_dir() else []:
        for cand in (d / "bin" / "python3", d / "python.exe"):
            if cand.is_file():
                return cand
    return None


def _copy_node_project(dest: Path, files: list[str]) -> None:
    for rel in files:
        src = REPO_ROOT / rel
        if src.is_file():
            (dest / rel).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dest / rel)


def _node_projects() -> list[tuple[str, list[str], str]]:
    pkg = json.loads((REPO_ROOT / "package.json").read_text())
    root = ["package.json", "package-lock.json", ".npmrc"] + [f"{w}/package.json" for w in pkg["workspaces"]]
    out = [("workspaces", root, ".")]
    if (REPO_ROOT / "apps/mobile/package-lock.json").is_file():
        out.append(("mobile", ["apps/mobile/package.json", "apps/mobile/package-lock.json", "apps/mobile/.npmrc"],
                    "apps/mobile"))
    return out


def node_coverage(lock: Path, cache: Path) -> dict:
    """Every package-lock entry needed on this platform must be present in the npm cache (by integrity hash)."""
    data = json.loads(lock.read_text())
    have, missing, other_platform = 0, [], 0
    sysmap = {"linux": "linux", "darwin": "darwin", "win32": "win32"}
    my_os = sysmap.get(sys.platform, sys.platform)
    my_cpu = {"x86_64": "x64", "amd64": "x64", "aarch64": "arm64", "arm64": "arm64"}.get(platform.machine().lower(),
                                                                                         platform.machine().lower())
    for name, e in (data.get("packages") or {}).items():
        if not name or e.get("link") or not e.get("integrity"):
            continue
        if e.get("optional") and ((e.get("os") and my_os not in e["os"]) or (e.get("cpu") and my_cpu not in e["cpu"])):
            other_platform += 1
            continue
        algo, b64 = e["integrity"].split()[0].split("-", 1)
        hexd = base64.b64decode(b64).hex()
        if (cache / "_cacache" / "content-v2" / algo / hexd[:2] / hexd[2:4] / hexd[4:]).is_file():
            have += 1
        else:
            missing.append(name)
    return {"lock": str(lock.relative_to(REPO_ROOT)), "cached": have, "missing": missing[:20],
            "missing_count": len(missing), "other_platform_optional_skipped": other_platform}


def _node(out: Path) -> dict:
    if not shutil.which("npm"):
        return {"status": "BLOCKED", "reason": "npm not installed"}
    cache = out / "npm-cache"
    cov = {"status": "INCLUDED"}
    for label, files, sub in _node_projects():
        with tempfile.TemporaryDirectory() as tmp:
            _copy_node_project(Path(tmp), files)
            r = _run(["npm", "ci", "--ignore-scripts", "--no-audit", "--no-fund", "--loglevel=error", "--logs-max=0",
                      "--cache", str(cache)], cwd=Path(tmp) / sub, timeout=3600,
                     env={**os.environ, "ELECTRON_SKIP_BINARY_DOWNLOAD": "1"})
            if r.returncode != 0:
                raise RuntimeError(f"npm cache population failed ({label}): {r.stderr[-1500:]}")
        lock = REPO_ROOT / sub / "package-lock.json"
        cov[label] = node_coverage(lock, cache)
        if cov[label]["missing_count"]:
            raise RuntimeError(f"npm cache incomplete for {label}: {cov[label]['missing'][:5]}")
    return cov


def _browsers(out: Path) -> dict:
    src = os.environ.get("PLAYWRIGHT_BROWSERS_PATH") or str(Path.home() / ".cache" / "ms-playwright")
    src_p = Path(src)
    if not src_p.is_dir():
        return {"status": "BLOCKED", "reason": f"no Playwright browser directory at {src}"}
    dirs = [d for d in src_p.iterdir() if d.is_dir() and d.name.startswith(("chromium", "ffmpeg"))]
    if not dirs:
        return {"status": "BLOCKED", "reason": "no Chromium build installed for Playwright"}
    for d in dirs:
        shutil.copytree(d, out / "browsers" / d.name, symlinks=True, dirs_exist_ok=True)
    return {"status": "INCLUDED", "builds": sorted(d.name for d in dirs)}


def _apt_closure(packages: list[str]) -> list[str]:
    """apt's own resolver, simulated against an EMPTY package database (so the result does not depend on what this
    build machine happens to have installed), without recommends. Base-system packages (priority required or
    important) are assumed present on every Debian/Ubuntu target and left out."""
    r = _run(["apt-get", "-s", "-o", "Dir::State::status=/dev/null", "--no-install-recommends", "install", *packages],
             timeout=600)
    if r.returncode != 0:
        raise RuntimeError(f"apt could not resolve {packages}: {r.stderr[-500:]}")
    names = sorted({ln.split()[1] for ln in r.stdout.splitlines() if ln.startswith("Inst ")})
    show = _run(["apt-cache", "show", "--no-all-versions", *names], timeout=600).stdout
    prio, cur = {}, None
    for ln in show.splitlines():
        if ln.startswith("Package:"):
            cur = ln.split(":", 1)[1].strip()
        elif ln.startswith("Priority:") and cur:
            prio[cur] = ln.split(":", 1)[1].strip()
    return [n for n in names if prio.get(n) not in ("required", "important")]


def _write_apt_index(dest: Path, debs: list[Path]) -> None:
    """A flat apt repository index (equivalent of dpkg-scanpackages) so apt can resolve from the bundle offline."""
    entries = []
    for d in debs:
        control = _run(["dpkg-deb", "-f", str(d)], timeout=60).stdout.rstrip("\n")
        entries.append(f"{control}\nFilename: ./{d.name}\nSize: {d.stat().st_size}\nSHA256: {_sha(d)}\n")
    (dest / "Packages").write_text("\n".join(entries))


def _system_packages(out: Path) -> dict:
    spec = yaml.safe_load(SYSTEM_MANIFEST.read_text()).get("offline_system_packages", {})
    if sys.platform.startswith("linux") and shutil.which("apt-get") and spec.get("debian"):
        names = _apt_closure(spec["debian"])
        dest = out / "system" / "debs"
        dest.mkdir(parents=True, exist_ok=True)
        r = _run(["apt-get", "download", *names], cwd=dest, timeout=3600)
        if r.returncode != 0:
            return {"status": "BLOCKED", "reason": f"apt-get download failed: {r.stderr[-500:]}"}
        debs = sorted(dest.glob("*.deb"))
        _write_apt_index(dest, debs)
        return {"status": "INCLUDED", "manager": "apt", "requested": spec["debian"], "closure": len(names),
                "debs": len(debs), "index": "system/debs/Packages"}
    if os.name == "nt":
        return {"status": "OPERATOR_PROVIDED", "reason": "Windows system installers (PostgreSQL 16 + pgvector, Redis "
                "or Memurai) are not redistributable here; place them in system/windows/ before transfer",
                "expected": spec.get("windows", [])}
    return {"status": "BLOCKED", "reason": "no supported system package manager (apt) on this machine"}


def _container_images(out: Path) -> dict:
    images = yaml.safe_load(SYSTEM_MANIFEST.read_text()).get("container_images", [])
    if not shutil.which("docker") or _run(["docker", "info"], timeout=60).returncode != 0:
        return {"status": "BLOCKED", "reason": "no Docker daemon: images cannot be pulled, resolved to digests or saved",
                "images": [f"{i['image']}:{i['tag']}" for i in images]}
    dest = out / "images"
    dest.mkdir(exist_ok=True)
    saved = []
    for i in images:
        ref = f"{i['image']}:{i['tag']}"
        if _run(["docker", "pull", ref], timeout=3600).returncode != 0:
            raise RuntimeError(f"docker pull {ref} failed")
        digest = _run(["docker", "inspect", "--format", "{{index .RepoDigests 0}}", ref]).stdout.strip()
        tar = dest / (ref.replace("/", "_").replace(":", "_") + ".tar")
        if _run(["docker", "save", "-o", str(tar), ref], timeout=3600).returncode != 0:
            raise RuntimeError(f"docker save {ref} failed")
        saved.append({"ref": ref, "digest": digest, "file": f"images/{tar.name}"})
    return {"status": "INCLUDED", "images": saved}


# ------------------------------------------------------------------------------------------------ create / verify
def create(out: Path, *, include_node: bool = True, models: list[str] | None = None, python_runtime: bool = True,
           browsers: bool = True, system_packages: bool = True, images: bool = True) -> dict:
    out = out.resolve()                         # npm runs in a temp dir; a relative cache path would land there
    out.mkdir(parents=True, exist_ok=True)
    cov: dict = {"python_wheels": _python_wheels(out)}
    cov["python_runtime"] = _python_runtime(out) if python_runtime else {"status": "EXCLUDED", "reason": "--no-python"}
    cov["node"] = _node(out) if include_node else {"status": "EXCLUDED", "reason": "--no-node"}
    cov["browsers"] = _browsers(out) if browsers else {"status": "EXCLUDED", "reason": "--no-browsers"}
    cov["system_packages"] = _system_packages(out) if system_packages else {"status": "EXCLUDED"}
    cov["container_images"] = _container_images(out) if images else {"status": "EXCLUDED"}
    for rel in ("database", "configuration/locks"):
        shutil.copytree(REPO_ROOT / rel, out / "assets" / rel, dirs_exist_ok=True)
    cov["models"] = {"status": "EXCLUDED", "reason": "models are referenced in place; include only on request"}
    if models:
        (out / "models").mkdir(exist_ok=True)
        for m in models:                       # only on explicit operator request
            shutil.copy2(m, out / "models" / Path(m).name)
        cov["models"] = {"status": "INCLUDED", "files": [Path(m).name for m in models]}
    shutil.rmtree(out / "npm-cache" / "_logs", ignore_errors=True)    # npm debug logs are not bundle content
    files = {}
    for p in sorted(out.rglob("*")):
        if p.is_symlink():
            continue
        if p.is_file() and p.name != "bundle_manifest.json":
            files[p.relative_to(out).as_posix()] = {"sha256": _sha(p), "bytes": p.stat().st_size}
    manifest = {"format": FORMAT, "created_at": iso_utc(),
                "platform": {"os": platform.system(), "machine": platform.machine(),
                             "python": f"{sys.version_info.major}.{sys.version_info.minor}"},
                "python_lock_sha256": _sha(LOCK), "python_full_lock_sha256": _sha(FULL_LOCK),
                "node_lock_sha256": _sha(REPO_ROOT / "package-lock.json"), "coverage": cov,
                "notes": {k: v.get("reason") for k, v in cov.items() if isinstance(v, dict) and v.get("reason")},
                "files": files, "includes_models": bool(models)}
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
    if m.get("python_full_lock_sha256") not in (None, _sha(FULL_LOCK)):
        problems.append("bundle was built for a different full Python lock")
    if m.get("node_lock_sha256") != _sha(REPO_ROOT / "package-lock.json"):
        problems.append("bundle was built for a different Node lock")
    present = {p.relative_to(bundle).as_posix() for p in bundle.rglob("*") if p.is_file() and not p.is_symlink()} \
        - {"bundle_manifest.json"}
    for rel, meta in m["files"].items():
        p = bundle / rel
        if not p.is_file():
            problems.append(f"missing: {rel}")
        elif p.stat().st_size != meta["bytes"] or _sha(p) != meta["sha256"]:
            problems.append(f"modified: {rel}")
    for extra in sorted(present - set(m["files"])):
        problems.append(f"unlisted file: {extra}")
    for rel in present:
        p = bundle / rel
        # Certificates (.pem trust stores) are fine; any file holding private-key material is not.
        if p.suffix == ".key" or (p.stat().st_size < 8 << 20 and any(x in p.read_bytes() for x in EXCLUDE_MARKERS)):
            problems.append(f"private key material in bundle: {rel}")
    return problems


# ------------------------------------------------------------------------------------------------ install
def install_offline(bundle: Path, venv: Path, *, full: bool = False) -> dict:
    """Fresh virtual environment from the bundle with every package index disabled (no network needed). Uses the
    bundled CPython when present, so the target needs no Python of its own."""
    problems = verify(bundle)
    if problems:
        raise RuntimeError("bundle verification failed: " + "; ".join(problems[:5]))
    base = Path(sys.executable)
    runtime = bundled_python(bundle)
    if runtime is not None:
        # Copy the relocatable CPython out of the bundle: the venv must not depend on (or write into) the bundle.
        dest = venv.parent / "python-runtime"
        src_root = runtime.parents[1] if runtime.parent.name == "bin" else runtime.parent
        shutil.copytree(src_root, dest / src_root.name, symlinks=True, dirs_exist_ok=True)
        base = dest / src_root.name / runtime.relative_to(src_root)
    r = _run([str(base), "-B", "-m", "venv", str(venv)], timeout=600)
    if r.returncode != 0:
        raise RuntimeError(f"venv creation with {base} failed: {r.stderr[-800:]}")
    py = venv / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    lock = FULL_LOCK if full else LOCK
    r = _run([str(py), "-m", "pip", "install", "--disable-pip-version-check", "-q", "--no-index", "--find-links",
              str(bundle / "wheels"), "--require-hashes", "--no-deps", "-r", str(lock)], timeout=3600)
    if r.returncode != 0:
        raise RuntimeError(f"offline pip install failed: {r.stderr[-1500:]}")
    site = _run([str(py), "-c", "import sysconfig;print(sysconfig.get_paths()['purelib'])"]).stdout.strip()
    Path(site, "atta_platform.pth").write_text(str(REPO_ROOT) + "\n")
    chk = _run([str(py), "-I", "-c", "import backend.api.app, backend.orchestration.controller, psycopg, fastapi; "
                "import sys; print(sys.version.split()[0])"], timeout=120)
    if chk.returncode != 0:
        raise RuntimeError(f"offline install import check failed: {chk.stderr[-800:]}")
    result = {"python": chk.stdout.strip(), "interpreter": "bundled" if bundled_python(bundle) else "host",
              "lock": lock.name}
    if (bundle / "npm-cache").is_dir() and shutil.which("npm"):
        # npm writes index entries into its cache: work on a private copy so the bundle stays byte-identical.
        cache = venv.parent / "npm-cache-copy"
        shutil.copytree(bundle / "npm-cache", cache, dirs_exist_ok=True)
        node_dir = venv.parent / "node-offline"
        for label, files, sub in _node_projects():
            _copy_node_project(node_dir, files)
            r = _run(["npm", "ci", "--offline", "--ignore-scripts", "--no-audit", "--no-fund", "--loglevel=error",
                      "--logs-max=0", "--cache", str(cache.resolve())], cwd=node_dir / sub, timeout=3600,
                     env={**os.environ, "ELECTRON_SKIP_BINARY_DOWNLOAD": "1"})
            if r.returncode != 0:
                raise RuntimeError(f"offline npm ci failed ({label}): {r.stderr[-1500:]}")
        if not (node_dir / "node_modules" / "next" / "package.json").is_file():
            raise RuntimeError("offline npm ci finished but the web framework package is missing")
        result["node"] = [label for label, _, _ in _node_projects()]
    if (bundle / "browsers").is_dir():
        result["browsers"] = str(bundle / "browsers")     # used via PLAYWRIGHT_BROWSERS_PATH, nothing to copy
    return result


def install_system(bundle: Path, simulate: bool = False) -> str:
    """Install the bundled system packages (administrator) from the bundle used as a local apt repository: apt's
    resolver picks exactly what the requested packages need, from the bundle only (no network sources)."""
    debs = (bundle / "system" / "debs").resolve()
    if not (debs / "Packages").is_file():
        raise RuntimeError("bundle contains no system package repository for this platform")
    if os.geteuid() != 0 and not simulate:
        raise RuntimeError("installing system packages needs administrator rights (run as root)")
    spec = yaml.safe_load(SYSTEM_MANIFEST.read_text()).get("offline_system_packages", {})
    with tempfile.TemporaryDirectory() as tmp:
        lists = Path(tmp) / "lists"
        (lists / "partial").mkdir(parents=True)
        src = Path(tmp) / "bundle.list"
        src.write_text(f"deb [trusted=yes] file:{debs} ./\n")
        opts = ["-o", f"Dir::Etc::SourceList={src}", "-o", "Dir::Etc::SourceParts=-", "-o", f"Dir::State::Lists={lists}",
                "-o", "APT::Get::List-Cleanup=0"]
        env = {**os.environ, "DEBIAN_FRONTEND": "noninteractive"}
        r = _run(["apt-get", *opts, "update"], timeout=600, env=env)
        if r.returncode != 0:
            raise RuntimeError(f"apt could not read the bundled repository: {r.stderr[-800:]}")
        # --simulate: re-install everything requested from the bundle alone (proves the bundle can supply it even on
        # a machine where the packages are already present).
        extra = ["-s", "--reinstall"] if simulate else []
        r = _run(["apt-get", *opts, "install", "-y", *extra, "--no-install-recommends", "-o",
                  "Dpkg::Options::=--force-confold", *spec.get("debian", [])], timeout=3600, env=env)
    if r.returncode != 0:
        raise RuntimeError(f"offline system package installation failed: {r.stderr[-1500:]}")
    return r.stdout[-600:]
