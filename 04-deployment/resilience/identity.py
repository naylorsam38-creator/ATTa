from __future__ import annotations
import hashlib, json, secrets, time
from pathlib import Path

# The code folder this module runs from (04-deployment). Under Coolify it is /opt/atta/04-deployment inside
# the image; on a plain server it is the folder systemd starts from. It is the code actually executing.
CODE_DIR = Path(__file__).resolve().parents[1]
# Code that decides a verdict. A change to any of these files is a new deployment revision.
_CODE_GLOBS = ("*.py", "resilience/*.py", "deployd/**/*.py", "upstream_apps.json", "app_catalogue.json")


def code_revision(code_dir: Path | str | None = None) -> str:
    """Content identity of the running ATTa code: release.json next to the code folder plus every file
    that decides a verdict. v121.1 looked only under APP_BUILDER_ROOT for release.json; inside the Coolify
    container nothing is ever put there, so every app read "unknown" and was marked UNVERIFIED."""
    code_dir = Path(code_dir) if code_dir else CODE_DIR
    h = hashlib.sha256()
    seen = 0
    rel = code_dir.parent / "release.json"
    files = [rel] if rel.is_file() else []
    for g in _CODE_GLOBS:
        files += sorted(p for p in code_dir.glob(g) if p.is_file() and "__pycache__" not in p.parts)
    for p in dict.fromkeys(files):
        try:
            data = p.read_bytes()
        except OSError:
            continue
        name = "release.json" if p == rel else p.relative_to(code_dir).as_posix()
        h.update(name.encode() + b"\0" + hashlib.sha256(data).digest())
        seen += 1
    return "code:" + h.hexdigest()[:24] if seen else "unknown"


def deployment_revision(root: Path | str, code_dir: Path | str | None = None) -> str:
    """Stable identity for the currently deployed ATTa: the ADM-promoted release (when ADM manages this
    machine) plus the content of the code that is actually running. Never "unknown" while the running
    code can be read, so a missing release.json can't turn every verdict into UNVERIFIED."""
    root = Path(root)
    candidates = [root / "adm" / "current" / "release.json", root / "current" / "release.json", root / "app" / "release.json", root / "release.json"]
    release = None
    for p in candidates:
        try:
            if p.is_file():
                release = json.loads(p.read_text())
                break
        except (OSError, ValueError, json.JSONDecodeError):
            continue
    parts = []
    if release is not None:
        raw = json.dumps(release, sort_keys=True, separators=(",", ":")).encode()
        parts.append("release:" + hashlib.sha256(raw).hexdigest()[:24])
    else:
        try:
            cur = root / "adm" / "current"
            parts.append("path:" + str(cur.resolve(strict=True)))
        except OSError:
            pass
    code = code_revision(code_dir)
    if code != "unknown":
        parts.append(code)
    return "+".join(parts) or "unknown"


def _git_head(d: Path) -> str | None:
    """HEAD commit read from the repository files (no subprocess: this runs twice per app per check)."""
    g = d / ".git"
    try:
        if g.is_file():   # worktree / submodule: "gitdir: <path>"
            txt = g.read_text().strip()
            if txt.startswith("gitdir:"):
                g = (d / txt.split(":", 1)[1].strip()).resolve()
        head = (g / "HEAD").read_text().strip()
        if not head.startswith("ref:"):
            return head or None
        ref = head.split(":", 1)[1].strip()
        if (g / ref).is_file():
            return (g / ref).read_text().strip() or None
        packed = g / "packed-refs"
        if packed.is_file():
            for line in packed.read_text().splitlines():
                if line.endswith(" " + ref):
                    return line.split(" ", 1)[0]
        return "unborn:" + ref
    except OSError:
        return None


def app_revision(app_dir: Path | str) -> str:
    """Identity of ONE app as it is being tested: its source commit, the intake record of every change
    made to it, and the skin/port files stages 4 and 5 compare against. Deliberately not a whole-tree
    fingerprint: the runner creates .env files in the app folder while starting it, and that must not
    read as "the app changed under the check". A re-import (new commit, new intake) or a new skin does."""
    d = Path(app_dir)
    if not d.is_dir():
        return "missing"
    h = hashlib.sha256()
    head = _git_head(d)
    h.update(b"head\0" + (head or "none").encode())
    try:
        h.update(b"intake\0" + hashlib.sha256((d / ".atta-intake.json").read_bytes()).digest())
    except OSError:
        h.update(b"intake\0none")
    ui = d / ".ui-capability"
    if ui.is_dir():
        for p in sorted(x for x in ui.rglob("*") if x.is_file()):
            try:
                h.update(p.relative_to(ui).as_posix().encode() + b"\0" + hashlib.sha256(p.read_bytes()).digest())
            except OSError:
                h.update(p.relative_to(ui).as_posix().encode() + b"\0unreadable")
    return "app:" + h.hexdigest()[:24] + (":" + head[:12] if head else "")


def new_correlation_id() -> str:
    return "c-" + secrets.token_hex(8)


def verification_snapshot(root: Path | str, app: str, attempt: int = 1, app_dir: Path | str | None = None) -> dict:
    return {
        "deployment_revision": deployment_revision(root),
        "app_revision": app_revision(app_dir) if app_dir is not None else None,
        "app": str(app),
        "attempt": int(attempt),
        "captured_at": time.time(),
    }


def revision_stable(before: str, after: str) -> bool:
    return bool(before and after and before != "unknown" and before == after)
