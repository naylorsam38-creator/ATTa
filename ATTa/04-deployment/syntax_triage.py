#!/usr/bin/env python3
"""
syntax_triage.py — tells a simple syntax slip apart from a real breakage, before healing starts.

When a qualify-layer failure names an app, its .ui-capability overlay (the part this system
writes) is scanned with a checker for each file type. Any syntax errors found are attached to
the failure item as exact "file:line: message" lines, so:

  - tier 1 can fix mechanical ones with no AI (e.g. a JSON trailing comma),
  - the LLM tier gets the precise file and line instead of a vague "BROKEN AT ..." verdict,
  - and a clean scan means the problem is NOT syntax, so it is treated as a real failure.

File types with no checker are skipped (never guessed at) and their extensions are logged to
state/maintenance/unchecked_extensions.json, so you can see what your builds actually contain
and add a checker later: one line in CHECKERS.

Upstream app source (the git checkouts in library/) is never scanned or edited: those apps
aren't run from there, and the governing spec forbids modifying upstream source.
"""
from __future__ import annotations
import ast, json, os, shutil, subprocess, time
from pathlib import Path

import builds

# ===================== CONFIG — edit here, nothing below needs reading =====================
# Most files scanned per app (overlays are small; this is just a safety cap).
MAX_FILES = 400
# Largest file checked (bytes). Bigger files are skipped.
MAX_BYTES = 2_000_000
# Seconds allowed per external checker (node, bash).
CHECK_TIMEOUT = 20
# Folders never scanned.
SKIP_DIRS = {".self-heal-backups", "node_modules", ".git", "__pycache__"}
UNCHECKED_LOG = builds.ROOT / "state" / "maintenance" / "unchecked_extensions.json"
# ==========================================================================================


def _run(cmd: list[str]) -> str | None:
    """None = passes. Otherwise the checker's error text."""
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=CHECK_TIMEOUT)
    except subprocess.TimeoutExpired:
        return None  # a slow checker is not evidence of a syntax error
    if r.returncode == 0:
        return None
    return (r.stderr or r.stdout).strip()[-800:] or f"exit {r.returncode}"


def _py(p: Path) -> str | None:
    try:
        ast.parse(p.read_text(errors="replace"), filename=p.name)
    except SyntaxError as e:
        return f"line {e.lineno}: {e.msg}"
    return None


def _json(p: Path) -> str | None:
    try:
        json.loads(p.read_text(errors="replace"))
    except json.JSONDecodeError as e:
        return f"line {e.lineno}: {e.msg}"
    return None


def _yaml(p: Path) -> str | None:
    try:
        import yaml
    except ImportError:
        return None
    try:
        yaml.safe_load(p.read_text(errors="replace"))
    except yaml.YAMLError as e:
        mark = getattr(e, "problem_mark", None)
        return (f"line {mark.line + 1}: " if mark else "") + str(getattr(e, "problem", e))
    return None


def _node(p: Path) -> str | None:
    node = shutil.which("node")
    return _run([node, "--check", str(p)]) if node else None


def _bash(p: Path) -> str | None:
    return _run(["bash", "-n", str(p)])


# Extension -> checker. To support a new language, add one line here.
CHECKERS = {
    ".py": _py,
    ".json": _json,
    ".yaml": _yaml, ".yml": _yaml,
    ".js": _node, ".mjs": _node, ".cjs": _node,
    ".sh": _bash,
}
# Plain content with no meaningful "syntax error": never logged as unchecked.
IGNORED = {".css", ".html", ".htm", ".md", ".txt", ".svg", ".png", ".jpg", ".jpeg", ".gif",
           ".webp", ".ico", ".woff", ".woff2", ".ttf", ".otf", ".map", ".log", ""}


def _tidy(err: str, p: Path) -> str:
    """One readable line: no absolute paths, no Node stack frames or version banner."""
    keep = []
    for ln in err.splitlines():
        t = ln.strip()
        if not t or t.startswith("at ") or t.startswith("Node.js v") or set(t) <= {"^", " "}:
            continue
        t = t.replace(str(p) + ": line ", "line ").replace(str(p) + ":", "line ").replace(str(p), p.name)
        keep.append(t)
    return " | ".join(keep)[:400]


def check_file(p: Path) -> str | None:
    """None = no syntax error found (or no checker). Otherwise the error, as one line."""
    fn = CHECKERS.get(p.suffix.lower())
    if not fn or not p.is_file() or p.stat().st_size > MAX_BYTES:
        return None
    try:
        err = fn(p)
    except Exception:
        return None  # a checker crash is not proof of a syntax error
    return _tidy(err, p) if err else None


def has_checker(p: Path) -> bool:
    return p.suffix.lower() in CHECKERS


def _log_unchecked(exts: set[str]) -> None:
    if not exts:
        return
    try:
        d = json.loads(UNCHECKED_LOG.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        d = {}
    for e in exts:
        d[e] = {"count": d.get(e, {}).get("count", 0) + 1, "last_seen": time.time()}
    UNCHECKED_LOG.parent.mkdir(parents=True, exist_ok=True)
    UNCHECKED_LOG.write_text(json.dumps(d, indent=2) + "\n")


def scan(base: Path) -> list[dict]:
    """Every syntax error under `base`: [{"path": relative, "error": text}]."""
    errors, unchecked, n = [], set(), 0
    base = Path(base)
    if not base.is_dir():
        return []
    for root, dirs, files in os.walk(base):
        dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS)
        for f in sorted(files):
            p = Path(root) / f
            ext = p.suffix.lower()
            if ext not in CHECKERS:
                if ext not in IGNORED:
                    unchecked.add(ext)
                continue
            n += 1
            if n > MAX_FILES:
                break
            err = check_file(p)
            if err:
                errors.append({"path": p.relative_to(base).as_posix(), "error": err})
    _log_unchecked(unchecked)
    return errors


def enrich(layer: str, item: dict) -> dict:
    """Attach syntax findings to a qualify-layer failure item. Returns the (possibly new) item.

    Adds item["syntax_errors"] (list) and appends "SYNTAX_ERROR <path>: <error>" lines to detail,
    which tier-1 rules and the LLM both read. A clean scan sets syntax_errors to [] so everyone
    downstream knows syntax was ruled out."""
    if layer != "qualify" or not item.get("app") or "syntax_errors" in item:
        return item
    import repair_actions as ra
    try:
        ui = ra.ui_dir(item["app"])
    except Exception:
        return item
    errs = scan(ui)
    out = {**item, "syntax_errors": errs}
    if errs:
        lines = "\n".join(f"SYNTAX_ERROR {e['path']}: {e['error']}" for e in errs[:20])
        out["detail"] = (lines + "\n" + (item.get("detail") or ""))[:4000]
    return out
