"""Capability package integrity: a deterministic SHA-256 over every file in the package directory.

The same algorithm is re-implemented (stdlib only) in backend/capability_runtime/child.py, which recomputes it over
the exact bytes it is about to execute; tests/unit asserts both produce identical digests."""
from __future__ import annotations

import hashlib
import os
from pathlib import Path

MAX_FILES = 500
MAX_FILE_BYTES = 5 * 1024 * 1024
SKIP_DIRS = {"__pycache__"}
SKIP_SUFFIXES = (".pyc", ".pyo")


class PackageError(ValueError):
    pass


def package_files(root: Path) -> list[tuple[str, Path]]:
    out: list[tuple[str, Path]] = []
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        d = Path(dirpath)
        for name in list(dirnames):
            if (d / name).is_symlink():
                raise PackageError(f"symlinked directory in package: {(d / name).relative_to(root)}")
        dirnames[:] = sorted(n for n in dirnames if n not in SKIP_DIRS)
        for name in sorted(filenames):
            p = d / name
            if name.endswith(SKIP_SUFFIXES):
                continue
            rel = p.relative_to(root).as_posix()
            if p.is_symlink():
                raise PackageError(f"symlink in package (resolves outside the capability directory?): {rel}")
            if p.stat().st_size > MAX_FILE_BYTES:
                raise PackageError(f"file too large for a capability package: {rel}")
            out.append((rel, p))
            if len(out) > MAX_FILES:
                raise PackageError(f"package has more than {MAX_FILES} files")
    return sorted(out)


def digest_entries(entries: list[tuple[str, bytes]]) -> str:
    h = hashlib.sha256()
    for rel, data in sorted(entries):
        h.update(rel.encode() + b"\0" + hashlib.sha256(data).hexdigest().encode() + b"\n")
    return h.hexdigest()


def package_digest(root: Path) -> str:
    return digest_entries([(rel, p.read_bytes()) for rel, p in package_files(root)])
