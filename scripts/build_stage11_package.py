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
NAME = "ATTa-stage11-additions"
STAMP = (2026, 11, 25, 0, 0, 0)


def git(*a: str) -> str:
    return subprocess.run(["git", "-C", str(ROOT), *a], capture_output=True, text=True, check=True).stdout


def changed_files() -> tuple[list[str], list[str]]:
    added = set(git("diff", "--name-only", "--diff-filter=A", BASELINE).split())
    added |= set(git("ls-files", "--others", "--exclude-standard").split())
    modified = set(git("diff", "--name-only", "--diff-filter=M", BASELINE).split())
    deleted = git("diff", "--name-only", "--diff-filter=DR", BASELINE).split()
    if deleted:
        raise SystemExit(f"Stage 11 must not delete or rename baseline files: {deleted}")
    return sorted(added), sorted(modified)


def protected_paths() -> list[str]:
    road = yaml.safe_load((ROOT / "configuration/build/roadmap.yaml").read_text())
    s01a = yaml.safe_load((ROOT / "configuration/build/stages/S01A.yaml").read_text())
    return sorted(set(road["protected_criteria"]) | set(s01a["protected_paths"]))


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
    (pkg / "PACKAGE.json").write_text(json.dumps(meta, indent=1))
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
