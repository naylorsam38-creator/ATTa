"""OS-permission protection of verification criteria (correction 8 / Stage 01A §24.8, T51).

Model: the VERIFIER (operator) account owns acceptance tests, verification logic, build-controller definitions,
the ledger, trusted controller configuration and the signing key; the implementation WORKER account (the one an
LLM or developer works as) can edit the rest of the tree but none of those. This module checks that the
filesystem really enforces that — it does not grant anything. Applied by scripts/protect_verifier.sh (root)."""
from __future__ import annotations

import grp
import os
import pwd
import stat
from pathlib import Path

PROTECTED_EXTRA = ("configuration/controller.yaml", "configuration/permissions.yaml", "configuration/tool_numbers.yaml",
                   "configuration/capability_trust.json", "configuration/capability_trust_ed25519.pub",
                   "configuration/controller_operator_ed25519.pub", "evidence/build_controller",
                   # The interpreter and libraries the verifier and the tests run with must not be worker-writable.
                   ".venv", "node_modules", "apps/mobile/node_modules")


def _users(bc) -> tuple[str, str, Path]:
    r = bc.roadmap
    return (os.environ.get("ATTA_BUILD_VERIFIER_USER", r.get("verifier_user", "root")),
            os.environ.get("ATTA_BUILD_WORKER_USER", r.get("worker_user", "atta-builder")),
            Path(os.environ.get("ATTA_BUILD_SIGNING_KEY_FILE", r.get("signing_key_path",
                                                                     "/etc/atta-verifier/build_signing_ed25519.pem"))))


def protected_paths(bc) -> list[Path]:
    out = [p for p in bc.protected_files()] + [bc.lock_file]
    out += [bc.root / p for p in PROTECTED_EXTRA if (bc.root / p).exists()]
    for pattern in bc.roadmap.get("protected_criteria", []):   # the directories themselves
        base = pattern.split("*")[0].rstrip("/")
        if base and (bc.root / base).is_dir():
            out.append(bc.root / base)
    return sorted(set(out))


def _writable_by(st: os.stat_result, uid: int, gids: set[int]) -> bool:
    m = st.st_mode
    if uid == 0:
        return True
    if st.st_uid == uid:
        return bool(m & stat.S_IWUSR)
    if st.st_gid in gids:
        return bool(m & stat.S_IWGRP)
    return bool(m & stat.S_IWOTH)


def protection_problems(bc) -> list[str]:
    verifier, worker, key = _users(bc)
    problems = []
    try:
        v = pwd.getpwnam(verifier)
    except KeyError:
        return [f"verifier account '{verifier}' does not exist"]
    try:
        w = pwd.getpwnam(worker)
    except KeyError:
        return [f"worker account '{worker}' does not exist: run scripts/protect_verifier.sh"]
    if w.pw_uid == 0:
        return ["the worker account is root: OS permissions cannot constrain it"]
    if w.pw_uid == v.pw_uid:
        return ["worker and verifier are the same account"]
    wgids = {w.pw_gid} | {g.gr_gid for g in grp.getgrall() if worker in g.gr_mem}
    if v.pw_gid in wgids and v.pw_uid != 0:
        problems.append("the worker is a member of the verifier's primary group")
    checked_dirs: set[Path] = set()
    for p in protected_paths(bc):
        try:
            st = os.lstat(p)
        except OSError as exc:
            problems.append(f"{p}: {exc}")
            continue
        rel = p.relative_to(bc.root) if bc.root in p.parents or p == bc.root else p
        if stat.S_ISLNK(st.st_mode):
            problems.append(f"{rel} is a symlink")
        if st.st_uid != v.pw_uid:
            problems.append(f"{rel} is owned by uid {st.st_uid}, not the verifier")
        if _writable_by(st, w.pw_uid, wgids):
            problems.append(f"{rel} is writable by the worker")
        # Every ancestor must stop the worker from renaming or replacing the protected entry.
        for d in [p.parent, *p.parent.parents]:
            if d in checked_dirs or (bc.root not in d.parents and d != bc.root):
                continue
            checked_dirs.add(d)
            ds = os.stat(d)
            if _writable_by(ds, w.pw_uid, wgids) and not (ds.st_mode & stat.S_ISVTX and ds.st_uid == v.pw_uid):
                problems.append(f"directory {d.relative_to(bc.root) if d != bc.root else '.'} lets the worker "
                                "rename or replace protected entries (needs verifier ownership + sticky bit, or no write)")
    try:
        ks = os.stat(key)
        if ks.st_uid != v.pw_uid or ks.st_mode & 0o077:
            problems.append(f"signing key {key} must be owned by the verifier with mode 0600")
        kd = os.stat(key.parent)
        if kd.st_uid != v.pw_uid or kd.st_mode & 0o077:
            problems.append(f"signing key directory {key.parent} must be verifier-owned with mode 0700")
        if bc.root in key.resolve().parents:
            problems.append("signing key is inside the repository")
    except OSError:
        problems.append(f"signing key {key} not found")
    return problems
