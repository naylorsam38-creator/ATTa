#!/usr/bin/env python3
"""Build the ATTa Stage 11 additions package (deterministic zip).

    python scripts/build_stage11_package.py --out <dir> [--release]

Contents are taken from the working tree relative to the Stage 01A baseline:
  overlay/<path>         every file added since the baseline
  base/<path>, ours/<path>   baseline and Stage 11 versions of every existing file Stage 11 changes (3-way merge input)
  patches/               the same changes as a unified diff (+ the commits as an mbox in --release mode)
  install.py/.sh/.bat/.ps1, INSTALL.md, compat/   installer, wrappers, instructions, compatibility harness
  PACKAGE.json           metadata (baseline, sidecar commit, file lists, protected paths)
  SHA256SUMS             SHA-256 of every other file — verified by install.py before anything happens
--release requires a clean working tree and records HEAD as the sidecar commit."""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
BASELINE = "21104c413c30a3b2714f877892970e7db19a1d19"
NAME = "ATTa-STAGE-11-COMPLETE"
STAMP = (2026, 11, 25, 0, 0, 0)
# Exactly what Stage 11 owns. Only files under these paths are packaged as additions, so the builder produces the
# same package from the sidecar and from a tree that also contains other stages (Stage 01B, Stage 04, ...).
STAGE11_PREFIXES = ("backend/research/", "capabilities/research_web/", "capabilities/research_evidence/",
                    "tests/research/", "documentation/stage11/", "packaging/stage11/", "evidence/stage_11/")
STAGE11_FILES = ("scripts/build_stage11_package.py", "configuration/research.yaml",
                 "database/migrations/0011_stage11_research.sql", "STAGE_11_CHANGE_MANIFEST.md",
                 "STAGE_11_MERGE_MANIFEST.md", "STAGE_11_VERIFICATION.md")
STAGE11_SHARED = ("BLOCKERS.md", "CHANGE_MANIFEST.md", "DECISIONS.md", "IMPLEMENTATION_PROGRESS.md",
                  "MASTER_IMPLEMENTATION_REGISTER.md", "documentation/requirements/requirement_register.yaml")


def owned(rel: str) -> bool:
    return rel.startswith(STAGE11_PREFIXES) or rel in STAGE11_FILES


def git(*a: str) -> str:
    return subprocess.run(["git", "-C", str(ROOT), *a], capture_output=True, text=True, check=True).stdout


def changed_files() -> tuple[list[str], list[str]]:
    added = set(git("diff", "--name-only", "--diff-filter=A", BASELINE).split())
    added |= set(git("ls-files", "--others", "--exclude-standard").split())
    modified = set(git("diff", "--name-only", "--diff-filter=M", BASELINE).split())
    deleted = set(git("diff", "--name-only", "--diff-filter=DR", BASELINE).split())
    bad = sorted(r for r in (modified | deleted) if owned(r)) + sorted(r for r in deleted if r in STAGE11_SHARED)
    if bad:
        raise SystemExit(f"Stage 11 paths must be new files and shared documents must exist: {bad}")
    missing = [r for r in STAGE11_SHARED if r not in modified]
    if missing:
        raise SystemExit(f"shared documents without Stage 11 changes: {missing}")
    return sorted(r for r in added if owned(r)), sorted(r for r in modified if r in STAGE11_SHARED)


def protected_paths() -> list[str]:
    road = yaml.safe_load((ROOT / "configuration/build/roadmap.yaml").read_text())
    s01a = yaml.safe_load((ROOT / "configuration/build/stages/S01A.yaml").read_text())
    return sorted(set(road["protected_criteria"]) | set(s01a["protected_paths"]))


_B, _K = b"-----" + b"BEGIN ", b"PRIVATE " + b"KEY-----"     # assembled so this file never matches itself
SECRET_PATTERNS = tuple(_B + kind + _K for kind in (b"", b"RSA ", b"OPENSSH ", b"EC ", b"ENCRYPTED ", b"DSA "))
SECRET_NAMES = ("local.env", ".env", "id_rsa", "id_ed25519")


def secret_scan(pkg: Path) -> list[str]:
    """Refuse to ship private keys, credential files or this environment's database passwords."""
    bad = []
    local = ROOT / "configuration" / "local.env"
    pw = [ln.split("=", 1)[1].strip().encode() for ln in (local.read_text().splitlines() if local.is_file() else [])
          if "PASSWORD" in ln and "=" in ln and ln.split("=", 1)[1].strip()]
    for p in pkg.rglob("*"):
        if not p.is_file():
            continue
        rel = p.relative_to(pkg).as_posix()
        if p.name in SECRET_NAMES or p.suffix in (".pem", ".key", ".p12", ".pfx"):
            bad.append(f"{rel}: credential/key file")
            continue
        data = p.read_bytes()
        if any(sig in data for sig in SECRET_PATTERNS):
            bad.append(f"{rel}: contains a private key")
        if any(x and len(x) >= 6 and x in data for x in pw):
            bad.append(f"{rel}: contains a configured database password")
    return bad


def build(out_dir: Path, release: bool = False) -> Path:
    if release and git("status", "--porcelain").strip():
        raise SystemExit("--release needs a clean working tree")
    head = git("rev-parse", "HEAD").strip()
    added, modified = changed_files()
    pkg = out_dir / NAME
    if pkg.exists():
        shutil.rmtree(pkg)
    for rel in added:
        dst = pkg / "overlay" / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / rel, dst)
    for rel in modified:
        for side, data in (("base", subprocess.run(["git", "-C", str(ROOT), "show", f"{BASELINE}:{rel}"],
                                                   capture_output=True, check=True).stdout),
                           ("ours", (ROOT / rel).read_bytes())):
            dst = pkg / side / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            dst.write_bytes(data)
    (pkg / "patches").mkdir(parents=True)
    (pkg / "patches" / "stage11-shared-files.patch").write_text(git("diff", BASELINE, "--", *modified))
    if release:
        (pkg / "patches" / "stage11-full.mbox").write_text(git("format-patch", "--stdout", f"{BASELINE}..HEAD"))
    src = ROOT / "packaging" / "stage11"
    for name in ("install.py", "install.sh", "install.bat", "install.ps1", "INSTALL.md"):
        shutil.copyfile(src / name, pkg / name)
    shutil.copytree(src / "compat", pkg / "compat", ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    meta = {"name": NAME, "stage": "S11 (roadmap S08): web search and deep research, CAP-11/CAP-12",
            "baseline_commit": BASELINE, "sidecar_commit": head if release else head + "-worktree",
            "release": release, "added": added, "modified": modified, "deleted": [],
            "protected_paths": protected_paths(),
            "merge_rules": {"BLOCKERS.md": "union", "DECISIONS.md": "union", "IMPLEMENTATION_PROGRESS.md": "union",
                            "CHANGE_MANIFEST.md": "union",
                            "documentation/requirements/requirement_register.yaml": "3way",
                            "MASTER_IMPLEMENTATION_REGISTER.md": "derived:render-register"},
            "requires": {"python": ">=3.8 (installer); 3.12-3.13 (ATTa)", "git": ">=2.17", "atta": "Stage 01A"}}
    if release:
        (pkg / "history").mkdir()
        subprocess.run(["git", "-C", str(ROOT), "bundle", "create", str(pkg / "history" / "stage11.bundle"),
                        f"{BASELINE}..HEAD"], check=True, capture_output=True)
        (pkg / "history" / "README.txt").write_text(
            "Recoverable Stage 11 commit history (prerequisite: Stage 01A baseline " + BASELINE + ").\n"
            "  git bundle verify stage11.bundle            (inside a clone that has the baseline)\n"
            "  git fetch stage11.bundle HEAD:refs/heads/stage11-restored\n")
    deps = {"new_python_packages": [], "new_node_packages": [], "new_services": [],
            "uses_locked": ["pydantic", "psycopg", "PyYAML", "jsonschema", "fastapi"],
            "handlers": "Python standard library only (capabilities/research_web, capabilities/research_evidence)",
            "installer": "Python >= 3.8 standard library + Git >= 2.17",
            "database": "PostgreSQL 16 (existing), migration database/migrations/0011_stage11_research.sql",
            "optional_external": ["a search provider (SearXNG JSON API) configured by the operator"]}
    (pkg / "DEPENDENCIES.json").write_text(json.dumps(deps, indent=1))
    meta["dependencies"] = "DEPENDENCIES.json"
    (pkg / "PACKAGE.json").write_text(json.dumps(meta, indent=1))
    leaks = secret_scan(pkg)
    if leaks:
        shutil.rmtree(pkg)
        raise SystemExit("refusing to package secrets:\n  " + "\n  ".join(leaks))
    (pkg / "ADDED_FILES.txt").write_text("\n".join(added) + "\n")
    (pkg / "MODIFIED_FILES.txt").write_text("\n".join(modified) + "\n")
    files = sorted(p for p in pkg.rglob("*") if p.is_file() and p != pkg / "SHA256SUMS")
    (pkg / "SHA256SUMS").write_text("".join(f"{hashlib.sha256(p.read_bytes()).hexdigest()}  "
                                            f"{p.relative_to(pkg).as_posix()}\n" for p in files))
    zpath = out_dir / f"{NAME}.zip"
    with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED) as z:
        for p in sorted(x for x in pkg.rglob("*") if x.is_file()):
            info = zipfile.ZipInfo(f"{NAME}/{p.relative_to(pkg).as_posix()}", STAMP)
            info.external_attr = (0o755 if p.suffix in (".sh", ".py") else 0o644) << 16
            info.compress_type = zipfile.ZIP_DEFLATED
            z.writestr(info, p.read_bytes())
    return pkg


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--release", action="store_true")
    a = ap.parse_args()
    out = Path(a.out).resolve()
    out.mkdir(parents=True, exist_ok=True)
    pkg = build(out, a.release)
    z = out / f"{NAME}.zip"
    print(f"{pkg}\n{z}  sha256 {hashlib.sha256(z.read_bytes()).hexdigest()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
