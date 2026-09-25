#!/usr/bin/env python3
"""
overlay_integrity.py — v116: the code in an app's .ui-capability overlay is ATTa's, and only ATTa writes it.

An overlay holds data (skin.css, skin.json, deployment.json, layouts) and CODE: run-ui.sh, which ATTa runs,
and ui-bridge/proxy.js + capability-port/port.js, which run-ui.sh runs under node. Before v116 an uploaded
app could ship its own .ui-capability/run-ui.sh, and the self-healing LLM could rewrite any overlay file,
launcher included; ATTa then ran it as root with every secret in its environment.

Now:
  - record(): right after the delivered package's installer (or intake) writes an overlay, the SHA-256 of
    every code file in it is stored outside the app's folder (state/overlay-integrity/<app>.json).
  - verify(): before any overlay code is started, the overlay must hold exactly those code files, byte for
    byte, and no symlinks. Anything else is refused and the app gets a real reinstall, not a guess.
  - is_code_path(): the self-healer may still fix DATA files (CSS, JSON); it may never write a code file.
Data files are not hashed: repairs change them legitimately, and nothing executes them.
"""
from __future__ import annotations
import hashlib, json, os, re, stat, tempfile
from pathlib import Path

# ===================== CONFIG — edit here, nothing below needs reading =====================
ROOT = Path(os.environ.get("APP_BUILDER_ROOT", "/srv/app-builder"))
REGISTRY = ROOT / "state" / "overlay-integrity"
# File types that are code: run by bash, node or python, or loadable by node's require().
CODE_SUFFIXES = (".sh", ".bash", ".js", ".mjs", ".cjs", ".ts", ".node", ".py", ".pl", ".rb", ".so", ".wasm")
# Self-heal backups live inside the overlay; they are never run and never hashed.
BACKUP_DIRNAME = ".self-heal-backups"
# ==========================================================================================


class IntegrityError(RuntimeError):
    pass


def _safe(app: str) -> str:
    return re.sub(r"[^a-z0-9-]+", "-", str(app).lower()).strip("-") or "app"


def is_code_path(rel: str) -> bool:
    """True for any overlay path the self-healer must never write (by name alone, before it exists)."""
    name = Path(str(rel)).name.lower()
    return name.endswith(CODE_SUFFIXES) or name in ("run-ui", "package.json", ".npmrc", "node_modules")


def _is_code_file(p: Path, st: os.stat_result) -> bool:
    if is_code_path(p.name) or st.st_mode & 0o111:
        return True
    try:
        with open(p, "rb") as f:
            return f.read(2) == b"#!"
    except OSError:
        return True


def _walk(ui: Path) -> tuple[dict[str, str], list[str]]:
    """({relative path: sha256} of every code file, [problems]) for one overlay."""
    code, problems = {}, []
    if not ui.is_dir() or ui.is_symlink():
        return code, [f"{ui} is not a real folder"]
    for dirpath, dirnames, filenames in os.walk(ui, followlinks=False):
        d = Path(dirpath)
        dirnames[:] = [n for n in dirnames if not (d == ui and n == BACKUP_DIRNAME)]
        for n in list(dirnames):
            if (d / n).is_symlink():
                problems.append(f"symlink {(d / n).relative_to(ui)}")
                dirnames.remove(n)
        for n in filenames:
            p = d / n
            rel = p.relative_to(ui).as_posix()
            st = os.lstat(p)
            if stat.S_ISLNK(st.st_mode):
                problems.append(f"symlink {rel}"); continue
            if not stat.S_ISREG(st.st_mode):
                problems.append(f"not a regular file: {rel}"); continue
            if _is_code_file(p, st):
                code[rel] = hashlib.sha256(p.read_bytes()).hexdigest()
    return code, problems


def record(app: str, ui_dir) -> dict:
    """Store the code-file hashes of an overlay ATTa itself just wrote. Refuses an overlay with symlinks."""
    ui = Path(ui_dir)
    code, problems = _walk(ui)
    if problems:
        raise IntegrityError(f"{_safe(app)} overlay not recorded: " + "; ".join(problems[:5]))
    if "run-ui.sh" not in code:
        raise IntegrityError(f"{_safe(app)} overlay has no run-ui.sh to record")
    REGISTRY.mkdir(parents=True, exist_ok=True)
    doc = {"schema": "ATTA_OVERLAY_INTEGRITY.v1", "app": _safe(app), "ui_dir": str(ui.resolve()), "code": code}
    fd, tmp = tempfile.mkstemp(dir=REGISTRY, prefix=".rec.")
    with os.fdopen(fd, "w") as f:
        json.dump(doc, f, indent=1, sort_keys=True)
    os.replace(tmp, REGISTRY / f"{_safe(app)}.json")
    return doc


def verify(app: str, ui_dir) -> None:
    """Raise IntegrityError unless the overlay's code files are exactly the recorded ones."""
    ui = Path(ui_dir)
    try:
        doc = json.loads((REGISTRY / f"{_safe(app)}.json").read_text())
    except (OSError, ValueError):
        raise IntegrityError(f"{_safe(app)}: no integrity record for its overlay (it was not written by ATTa's "
                             "installer); reinstall the overlay")
    if Path(doc.get("ui_dir", "")) != ui.resolve():
        raise IntegrityError(f"{_safe(app)}: overlay moved since it was recorded; reinstall the overlay")
    code, problems = _walk(ui)
    if problems:
        raise IntegrityError(f"{_safe(app)} overlay: " + "; ".join(problems[:5]))
    want = doc.get("code") or {}
    changed = sorted(k for k in set(code) | set(want) if code.get(k) != want.get(k))
    if changed:
        raise IntegrityError(f"{_safe(app)} overlay code differs from what ATTa installed: " + ", ".join(changed[:8]))


def forget(app: str) -> None:
    (REGISTRY / f"{_safe(app)}.json").unlink(missing_ok=True)
