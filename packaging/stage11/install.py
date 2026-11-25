#!/usr/bin/env python3
"""Transactional installer for the ATTa Stage 11 additions (web search and deep research).

Standard library only; Python >= 3.8; Windows, Linux and macOS. Requires Git.

    python install.py <ATTa repository root> --check      dry run: verify everything, rehearse, change nothing
    python install.py <ATTa repository root>              install (all or nothing)
    python install.py <ATTa repository root> --rollback-interrupted    undo an install that was killed midway

Guarantees
  * package integrity: every file is verified against SHA256SUMS (and no unlisted file may be present) before
    anything else happens;
  * target: must be the TOP LEVEL of its own Git repository (not a sub-directory, not nested inside another
    repository, no nested repository on any path written), with a CLEAN working tree (no modified, staged or
    untracked files, no merge/rebase in progress) and the Stage 01A runtime present;
  * no overwrite: an existing file that differs is a conflict; shared documents are 3-way merged (git merge-file)
    against the baseline and any merge conflict is a conflict; protected paths are never written;
  * --check exits non-zero whenever an unresolved conflict exists;
  * rehearsal: the complete installation is first performed in a temporary `git worktree` of the target's HEAD and
    verified there; the real tree is touched only if the rehearsal succeeded;
  * transaction: originals are backed up and a journal is written inside the repository's .git directory before
    the first write; any error, Ctrl-C or failed post-verification restores the original state and verifies the
    tree is clean again; a hard kill leaves the journal so --rollback-interrupted can restore it;
  * protected configuration is hashed before and after; any change aborts and rolls back.

Exit codes: 0 ok / already installed · 2 target or usage refused · 3 conflicts · 4 rehearsal failed ·
            5 install failed and was rolled back · 6 rollback incomplete (see message) · 7 package integrity failure
"""
from __future__ import annotations

import argparse
import fnmatch
import hashlib
import json
import os
import py_compile
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path, PurePosixPath

PKG = Path(__file__).resolve().parent
JOURNAL_DIR = "atta-stage11-install"
EXIT_OK, EXIT_REFUSED, EXIT_CONFLICT, EXIT_REHEARSAL, EXIT_ROLLED_BACK, EXIT_ROLLBACK_INCOMPLETE, EXIT_INTEGRITY = \
    0, 2, 3, 4, 5, 6, 7
REQUIRED_MARKERS = ("backend/orchestration/controller.py", "database/migrations/0002_runtime_controller.sql",
                    "configuration/controller.yaml", "backend/capability_registry/registry.py")


class Refused(Exception):
    def __init__(self, code: int, message: str):
        super().__init__(message)
        self.code = code


def say(msg: str) -> None:
    print(msg, flush=True)


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def rel_to_path(root: Path, rel: str) -> Path:
    p = PurePosixPath(rel)
    if p.is_absolute() or ".." in p.parts or not rel or "\\" in rel or ":" in rel:
        raise Refused(EXIT_INTEGRITY, f"unsafe path in package: {rel!r}")
    return root.joinpath(*p.parts)


# ------------------------------------------------------------------------------------------------ package
def verify_package() -> dict:
    sums_file = PKG / "SHA256SUMS"
    if not sums_file.is_file():
        raise Refused(EXIT_INTEGRITY, "SHA256SUMS missing: the package is incomplete")
    listed = {}
    for line in sums_file.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        digest, _, rel = line.partition("  ")
        rel = rel.strip()
        if len(digest) != 64 or not rel:
            raise Refused(EXIT_INTEGRITY, f"malformed SHA256SUMS line: {line!r}")
        listed[rel] = digest
    present = set()
    for dirpath, dirnames, filenames in os.walk(PKG):
        dirnames[:] = [d for d in dirnames if d != "__pycache__"]
        for fn in filenames:
            rel = Path(dirpath, fn).relative_to(PKG).as_posix()
            if rel != "SHA256SUMS":
                present.add(rel)
    missing, extra = sorted(set(listed) - present), sorted(present - set(listed))
    if missing or extra:
        raise Refused(EXIT_INTEGRITY, f"package contents differ from SHA256SUMS: missing={missing[:5]} "
                                      f"unlisted={extra[:5]}")
    bad = [rel for rel, d in listed.items() if sha256(rel_to_path(PKG, rel)) != d]
    if bad:
        raise Refused(EXIT_INTEGRITY, f"checksum mismatch (package altered or corrupted): {bad[:5]}")
    meta = json.loads((PKG / "PACKAGE.json").read_text(encoding="utf-8"))
    for rel in meta["added"]:
        if f"overlay/{rel}" not in listed:
            raise Refused(EXIT_INTEGRITY, f"PACKAGE.json lists {rel} but overlay/ does not contain it")
    for rel in meta["modified"]:
        for side in ("base", "ours"):
            if f"{side}/{rel}" not in listed:
                raise Refused(EXIT_INTEGRITY, f"{side}/{rel} missing from the package")
    folded = [r.casefold() for r in meta["added"] + meta["modified"]]
    if len(set(folded)) != len(folded):
        raise Refused(EXIT_INTEGRITY, "package paths collide on case-insensitive file systems")
    say(f"package verified: {len(listed)} files match SHA256SUMS (sidecar {meta.get('sidecar_commit', '?')[:12]})")
    return meta


# ------------------------------------------------------------------------------------------------ git helpers
def git(cwd: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    try:
        cp = subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True)
    except FileNotFoundError as exc:
        raise Refused(EXIT_REFUSED, "git is required but was not found on PATH") from exc
    if check and cp.returncode != 0:
        raise Refused(EXIT_REFUSED, f"git {' '.join(args)} failed: {cp.stderr.strip()[:500]}")
    return cp


def same_path(a: Path, b: Path) -> bool:
    return os.path.normcase(os.path.realpath(a)) == os.path.normcase(os.path.realpath(b))


def git_dir(target: Path) -> Path:
    d = Path(git(target, "rev-parse", "--git-dir").stdout.strip())
    return d if d.is_absolute() else (target / d)


# ------------------------------------------------------------------------------------------------ target checks
def check_target(target: Path, meta: dict, *, allow_journal: bool = False) -> None:
    if not target.is_dir():
        raise Refused(EXIT_REFUSED, f"target is not a directory: {target}")
    cp = git(target, "rev-parse", "--show-toplevel", check=False)
    if cp.returncode != 0:
        raise Refused(EXIT_REFUSED, "target is not a Git working tree (installation requires Git for the clean-tree "
                                    "guarantee and the rehearsal)")
    top = Path(cp.stdout.strip())
    if not same_path(top, target):
        raise Refused(EXIT_REFUSED, f"target must be the top level of its repository; its repository root is {top}")
    if git(target, "rev-parse", "--is-bare-repository").stdout.strip() == "true":
        raise Refused(EXIT_REFUSED, "bare repositories are not supported")
    if (target / ".git").is_file():
        raise Refused(EXIT_REFUSED, "target is a submodule or linked worktree (.git is a file); install into the "
                                    "primary checkout")
    outer = git(target.parent, "rev-parse", "--show-toplevel", check=False)
    if outer.returncode == 0:
        raise Refused(EXIT_REFUSED, f"target is nested inside another Git repository ({outer.stdout.strip()}); "
                                    "nested-repository targets are rejected")
    for marker in REQUIRED_MARKERS:
        if not (target / marker).is_file():
            raise Refused(EXIT_REFUSED, f"not an ATTa Stage 01A tree: {marker} is missing")
    gd = git_dir(target)
    for state in ("MERGE_HEAD", "CHERRY_PICK_HEAD", "REVERT_HEAD", "rebase-apply", "rebase-merge", "BISECT_LOG"):
        if (gd / state).exists():
            raise Refused(EXIT_REFUSED, f"a Git operation is in progress ({state}); finish or abort it first")
    if (gd / JOURNAL_DIR).exists() and not allow_journal:
        raise Refused(EXIT_REFUSED, "an interrupted Stage 11 installation was found; run with --rollback-interrupted")
    status = git(target, "status", "--porcelain", "--untracked-files=all").stdout
    if status.strip() and not allow_journal:
        raise Refused(EXIT_REFUSED, "the working tree is not clean (commit or stash first):\n  " +
                      "\n  ".join(status.splitlines()[:15]))
    if git(target, "rev-parse", "--verify", "-q", "HEAD", check=False).returncode != 0:
        raise Refused(EXIT_REFUSED, "the repository has no commits")
    base = meta.get("baseline_commit")
    if base and git(target, "cat-file", "-e", f"{base}^{{commit}}", check=False).returncode == 0:
        if git(target, "merge-base", "--is-ancestor", base, "HEAD", check=False).returncode != 0:
            raise Refused(EXIT_REFUSED, f"HEAD does not contain the Stage 01A baseline {base[:12]}")
    else:
        say(f"note: baseline commit {str(base)[:12]} not in this repository's history; relying on content checks")


def nested_repo_on_path(target: Path, rel: str) -> str | None:
    cur = target
    for part in PurePosixPath(rel).parts[:-1]:
        cur = cur / part
        if not cur.exists():
            return None
        if (cur / ".git").exists():
            return cur.relative_to(target).as_posix()
    return None


def is_protected(rel: str, patterns: list[str]) -> bool:
    return any(fnmatch.fnmatchcase(rel, p.replace("**", "*")) or rel == p.rstrip("/*") for p in patterns)


# ------------------------------------------------------------------------------------------------ planning
def eol_of(data: bytes) -> bytes:
    return b"\r\n" if b"\r\n" in data else b"\n"


def to_eol(data: bytes, eol: bytes) -> bytes:
    lf = data.replace(b"\r\n", b"\n")
    return lf if eol == b"\n" else lf.replace(b"\n", b"\r\n")


def merge3(current: bytes, base: bytes, ours: bytes, union: bool = False) -> tuple[bytes | None, int]:
    with tempfile.TemporaryDirectory() as t:
        tp = Path(t)
        (tp / "current").write_bytes(current)
        (tp / "base").write_bytes(base)
        (tp / "ours").write_bytes(ours)
        cp = subprocess.run(["git", "merge-file", "-p", *(["--union"] if union else []), "-L", "target", "-L",
                             "baseline", "-L", "stage11", str(tp / "current"), str(tp / "base"), str(tp / "ours")],
                            capture_output=True)
    if cp.returncode < 0 or cp.returncode > 127:
        raise Refused(EXIT_REFUSED, f"git merge-file failed: {cp.stderr.decode(errors='replace')[:300]}")
    return (cp.stdout if cp.returncode == 0 else None), cp.returncode


def plan(target: Path, meta: dict) -> dict:
    actions, conflicts, already = [], [], []
    protected = meta.get("protected_paths", [])
    for rel in meta["added"]:
        src = rel_to_path(PKG / "overlay", rel)
        dst = rel_to_path(target, rel)
        data = src.read_bytes()
        if is_protected(rel, protected):
            conflicts.append(f"{rel}: protected path (the package must never write it)")
            continue
        nested = nested_repo_on_path(target, rel)
        if nested:
            conflicts.append(f"{rel}: would be written inside the nested repository {nested}")
            continue
        blocker = next((a for a in [rel_to_path(target, "/".join(PurePosixPath(rel).parts[:i]))
                                    for i in range(1, len(PurePosixPath(rel).parts))] if a.is_file()), None)
        if blocker is not None:
            conflicts.append(f"{rel}: a file exists where a directory is needed ({blocker.relative_to(target)})")
        elif dst.is_dir():
            conflicts.append(f"{rel}: a directory exists at this path")
        elif dst.exists():
            (already if dst.read_bytes() == data else conflicts).append(
                rel if dst.read_bytes() == data else f"{rel}: exists with different content")
        else:
            actions.append({"kind": "create", "rel": rel, "data": data})
    rules = meta.get("merge_rules", {})
    for rel in meta["modified"]:
        rule = rules.get(rel, "3way")
        dst = rel_to_path(target, rel)
        if is_protected(rel, protected):
            conflicts.append(f"{rel}: protected path")
            continue
        if not dst.is_file():
            conflicts.append(f"{rel}: expected an existing file to update")
            continue
        current = dst.read_bytes()
        eol = eol_of(current)
        base = to_eol(rel_to_path(PKG / "base", rel).read_bytes(), eol)
        ours = to_eol(rel_to_path(PKG / "ours", rel).read_bytes(), eol)
        if current == ours:
            already.append(rel)
        elif current == base:
            actions.append({"kind": "update", "rel": rel, "data": ours, "merge": "fast-forward",
                            **({"derived": rule[8:]} if rule.startswith("derived:") else {})})
        else:
            merged, n = merge3(current, base, ours)
            how = "3-way"
            if merged is None and rule == "union":            # append-only log: both stages' sections are kept
                merged, n = merge3(current, base, ours, union=True)
                how = "union (append-only log)"
            if rule.startswith("derived:"):                   # regenerated from merged sources in the rehearsal
                actions.append({"kind": "update", "rel": rel, "data": merged, "merge": rule, "derived": rule[8:]})
            elif merged is None:
                conflicts.append(f"{rel}: {n} merge conflict(s) with changes already in the target; resolve manually "
                                 "(keep both sections) — nothing was changed")
            elif merged == current:
                already.append(rel)
            else:
                actions.append({"kind": "update", "rel": rel, "data": merged, "merge": how})
    return {"actions": actions, "conflicts": conflicts, "already": already}


def protected_snapshot(target: Path, patterns: list[str]) -> dict:
    out = {}
    files = git(target, "ls-files", "-z").stdout.split("\0")
    for rel in files:
        if rel and is_protected(rel, patterns):
            p = rel_to_path(target, rel)
            if p.is_file():
                out[rel] = sha256(p)
    return out


# ------------------------------------------------------------------------------------------------ apply / verify
def write_atomic(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".s11tmp")
    with open(tmp, "wb") as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def apply_actions(root: Path, actions: list[dict], journal: dict | None = None, journal_path: Path | None = None):
    real = journal is not None                                      # fault injection applies to the real install only
    fail_after = os.environ.get("ATTA_S11_INSTALL_FAIL_AFTER") if real else None    # TEST-ONLY fault injection
    crash_after = os.environ.get("ATTA_S11_INSTALL_CRASH_AFTER") if real else None  # TEST-ONLY hard kill simulation
    for n, a in enumerate(actions, 1):
        dst = rel_to_path(root, a["rel"])
        if journal is not None:
            new_dirs = []
            d = dst.parent
            while not d.exists():
                new_dirs.append(d.relative_to(root).as_posix())
                d = d.parent
            journal["created_dirs"] += new_dirs
            if a["kind"] == "create":
                journal["created"].append(a["rel"])
            save_journal(journal_path, journal)
        write_atomic(dst, a["data"])
        if fail_after and n >= int(fail_after):
            raise RuntimeError(f"injected failure after {n} writes (ATTA_S11_INSTALL_FAIL_AFTER)")
        if crash_after and n >= int(crash_after):
            os._exit(9)


def verify_tree(root: Path, actions: list[dict], *, compile_python: bool = True) -> list[str]:
    problems = []
    for a in actions:
        p = rel_to_path(root, a["rel"])
        if not p.is_file() or p.read_bytes() != a["data"]:
            problems.append(f"{a['rel']}: content differs from the planned content")
        elif compile_python and a["rel"].endswith(".py"):
            try:
                py_compile.compile(str(p), cfile=os.devnull if os.name != "nt" else None, doraise=True)
            except py_compile.PyCompileError as exc:
                problems.append(f"{a['rel']}: does not compile: {exc.msg[:200]}")
            except OSError:
                pass
        elif a["rel"].endswith(".json"):
            try:
                json.loads(p.read_bytes())
            except ValueError as exc:
                problems.append(f"{a['rel']}: invalid JSON: {exc}")
    changed = set()
    for line in git(root, "status", "--porcelain", "--untracked-files=all").stdout.splitlines():
        changed.add(line[3:].strip().strip('"'))
    expected = {a["rel"] for a in actions}
    unexpected = sorted(changed - expected)
    if unexpected:
        problems.append(f"unexpected changes in the tree: {unexpected[:10]}")
    return problems


def python_with_yaml(target: Path) -> str | None:
    for cand in (target / ".venv" / "bin" / "python", target / ".venv" / "Scripts" / "python.exe",
                 Path(sys.executable)):
        if cand.is_file() and subprocess.run([str(cand), "-c", "import yaml"], capture_output=True).returncode == 0:
            return str(cand)
    return None


def run_derived(target: Path, wt: Path, steps: dict) -> list[str]:
    """Regenerate derived files (the master register is rendered from the merged register YAML)."""
    problems = []
    for a in steps["actions"]:
        if a.get("derived") != "render-register":
            continue
        py = python_with_yaml(target)
        if py is None:
            if a["data"] is None:
                problems.append(f"{a['rel']}: conflicting generated file and no Python with PyYAML to regenerate it "
                                "(install the ATTa environment first)")
            continue
        r = subprocess.run([py, "scripts/requirement_register.py", "render"], cwd=wt, capture_output=True, text=True)
        c = subprocess.run([py, "scripts/requirement_register.py", "check"], cwd=wt, capture_output=True, text=True)
        if r.returncode or c.returncode:
            problems.append(f"{a['rel']}: register render/check failed: {(r.stdout + r.stderr + c.stdout)[-400:]}")
        else:
            a["data"] = to_eol((wt / a["rel"]).read_bytes(), eol_of(rel_to_path(target, a["rel"]).read_bytes()))
            write_atomic(wt / a["rel"], a["data"])
            a["merge"] = "regenerated from the merged register YAML"
    return problems


def rehearse(target: Path, steps: dict) -> None:
    tmp = Path(tempfile.mkdtemp(prefix="atta-s11-rehearsal-"))
    wt = tmp / "wt"
    try:
        git(target, "worktree", "add", "--detach", str(wt), "HEAD")
        apply_actions(wt, [a for a in steps["actions"] if a["data"] is not None])
        problems = run_derived(target, wt, steps)
        if problems:
            raise Refused(EXIT_CONFLICT, "unresolved conflicts:\n  " + "\n  ".join(problems))
        problems = verify_tree(wt, steps["actions"])
        if problems:
            raise Refused(EXIT_REHEARSAL, "rehearsal failed:\n  " + "\n  ".join(problems))
        say(f"rehearsal passed in a temporary worktree ({len(steps['actions'])} file operations verified)")
    except Refused:
        raise
    except Exception as exc:  # noqa: BLE001 — any rehearsal error means: do not touch the real tree
        raise Refused(EXIT_REHEARSAL, f"rehearsal failed: {type(exc).__name__}: {exc}") from exc
    finally:
        git(target, "worktree", "remove", "--force", str(wt), check=False)
        git(target, "worktree", "prune", check=False)
        shutil.rmtree(tmp, ignore_errors=True)


# ------------------------------------------------------------------------------------------------ journal / rollback
def save_journal(path: Path, journal: dict) -> None:
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(journal, indent=1), encoding="utf-8")
    os.replace(tmp, path)


def rollback(target: Path, jdir: Path) -> list[str]:
    jpath = jdir / "journal.json"
    journal = json.loads(jpath.read_text(encoding="utf-8"))
    problems = []
    for rel in journal["modified"]:
        bak = jdir / "backup" / rel
        try:
            write_atomic(rel_to_path(target, rel), bak.read_bytes())
        except OSError as exc:
            problems.append(f"could not restore {rel}: {exc}")
    for rel in journal["created"]:
        p = rel_to_path(target, rel)
        for q in (p, p.with_name(p.name + ".s11tmp")):
            try:
                if q.exists():
                    q.unlink()
            except OSError as exc:
                problems.append(f"could not remove {rel}: {exc}")
    for rel in sorted(set(journal["created_dirs"]), key=lambda r: -r.count("/")):
        d = rel_to_path(target, rel)
        try:
            if d.is_dir():
                for leftover in d.rglob("*.s11tmp"):
                    leftover.unlink()
                if not any(d.iterdir()):
                    d.rmdir()
        except OSError as exc:
            problems.append(f"could not remove directory {rel}: {exc}")
    status = git(target, "status", "--porcelain", "--untracked-files=all").stdout.strip()
    if status:
        problems.append("tree not clean after rollback:\n  " + "\n  ".join(status.splitlines()[:10]))
    if not problems:
        shutil.rmtree(jdir, ignore_errors=True)
    return problems


def install(target: Path, meta: dict, steps: dict) -> int:
    jdir = git_dir(target) / JOURNAL_DIR
    jdir.mkdir(parents=True)
    journal = {"package": meta.get("sidecar_commit"), "created": [], "created_dirs": [], "modified": []}
    for a in steps["actions"]:
        if a["kind"] == "update":
            bak = jdir / "backup" / a["rel"]
            bak.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(rel_to_path(target, a["rel"]), bak)
            journal["modified"].append(a["rel"])
    save_journal(jdir / "journal.json", journal)
    before = protected_snapshot(target, meta.get("protected_paths", []))
    try:
        apply_actions(target, steps["actions"], journal, jdir / "journal.json")
        problems = verify_tree(target, steps["actions"], compile_python=False)
        after = protected_snapshot(target, meta.get("protected_paths", []))
        if after != before:
            problems.append("protected configuration changed during installation")
        if problems:
            raise RuntimeError("post-installation verification failed:\n  " + "\n  ".join(problems))
    except BaseException as exc:  # noqa: BLE001 — includes KeyboardInterrupt: always roll back
        say(f"INSTALLATION FAILED: {exc}")
        left = rollback(target, jdir)
        if left:
            say("ROLLBACK INCOMPLETE:\n  " + "\n  ".join(left) +
                "\nThe journal was kept; run again with --rollback-interrupted.")
            return EXIT_ROLLBACK_INCOMPLETE
        say("rolled back: the target is exactly as before (working tree clean)")
        return EXIT_ROLLED_BACK
    shutil.rmtree(jdir, ignore_errors=True)
    say(f"installed: {sum(a['kind'] == 'create' for a in steps['actions'])} files added, "
        f"{sum(a['kind'] == 'update' for a in steps['actions'])} shared files updated "
        f"({', '.join(a['rel'] + ' [' + a['merge'] + ']' for a in steps['actions'] if a['kind'] == 'update')})")
    say("protected configuration verified unchanged")
    say("next steps (INSTALL.md): migrate the database, run the tests, then the operator integration steps")
    return EXIT_OK


# ------------------------------------------------------------------------------------------------ main
def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Install the ATTa Stage 11 additions (transactional).")
    ap.add_argument("target", help="ATTa repository root (top level of a clean Git working tree)")
    ap.add_argument("--check", action="store_true", help="dry run: verify and rehearse, change nothing")
    ap.add_argument("--rollback-interrupted", action="store_true", help="undo an installation that was killed")
    a = ap.parse_args(argv)
    target = Path(a.target).resolve()
    try:
        meta = verify_package()
        if a.rollback_interrupted:
            check_target(target, meta, allow_journal=True)
            jdir = git_dir(target) / JOURNAL_DIR
            if not (jdir / "journal.json").is_file():
                say("no interrupted installation found")
                return EXIT_OK
            left = rollback(target, jdir)
            if left:
                say("ROLLBACK INCOMPLETE:\n  " + "\n  ".join(left))
                return EXIT_ROLLBACK_INCOMPLETE
            say("interrupted installation rolled back; the working tree is clean")
            return EXIT_OK
        check_target(target, meta)
        steps = plan(target, meta)
        if steps["conflicts"]:
            say(f"UNRESOLVED CONFLICTS ({len(steps['conflicts'])}) — nothing was changed:\n  " +
                "\n  ".join(steps["conflicts"]))
            return EXIT_CONFLICT
        if not steps["actions"]:
            say(f"already installed: all {len(steps['already'])} files present and identical")
            return EXIT_OK
        say(f"plan: {sum(x['kind'] == 'create' for x in steps['actions'])} new files, "
            f"{sum(x['kind'] == 'update' for x in steps['actions'])} shared-file updates, "
            f"{len(steps['already'])} already present")
        rehearse(target, steps)
        if a.check:
            say("check passed: no conflicts; the installation would succeed (nothing was changed)")
            return EXIT_OK
        return install(target, meta, steps)
    except Refused as exc:
        say(f"REFUSED: {exc}")
        return exc.code


if __name__ == "__main__":
    sys.exit(main())
