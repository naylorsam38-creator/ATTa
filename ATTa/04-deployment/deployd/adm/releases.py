"""Code releases and the KNOWN-GOOD record (v117, checklists D and G).

bootstrap.sh installs each deploy's code into its own folder under RELS (/opt/app-builder-releases/<ver>-<time>)
and APP (/opt/app-builder) is a symlink to the live one. This module is the ONLY place that decides which of
those folders may be trusted:

  <release>/.atta-verified.json   written only after that release passed every check WHILE LIVE
  <release>/.atta-failed.json     written when a deploy of it failed (it is then deleted by prune)
  RELS/.known-good.json           the release to go back to: always a verified one, and always one that was live
  RELS/.previous-known-good.json  the known-good before that

Rules, enforced here (and tested in tests/test_v117_adm.py):
  - mark_verified() refuses a release that is not the one APP points at, or that was marked failed.
  - promote() refuses a release that isn't verified by THIS deployment and live right now.
  - A failed, interrupted, partial or untested release never becomes known-good or previous-known-good.
  - Rollback always targets the known-good recorded BEFORE the deploy began (the deployment record keeps it),
    never "the second newest folder".
  - prune() never deletes the live, known-good or previous-known-good release.

The first v117 deploy on a server that ran an older ATTa has no known-good yet: `adopt` records the release that
is live now as known-good, but only if it answered its health check right before (bootstrap/ADM check first),
and marks it "adopted" so the record says how it got there.
"""
from __future__ import annotations
import hashlib, json, os, shutil, sys
from datetime import datetime, timezone
from pathlib import Path

VERIFIED = ".atta-verified.json"
FAILED = ".atta-failed.json"
KNOWN_GOOD = ".known-good.json"
PREVIOUS_KNOWN_GOOD = ".previous-known-good.json"
_MARKERS = {VERIFIED, FAILED}


class ReleaseError(RuntimeError):
    pass


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _write_json(p: Path, doc):
    tmp = p.with_name(p.name + f".{os.getpid()}.tmp")
    tmp.write_text(json.dumps(doc, indent=2) + "\n")
    os.chmod(tmp, 0o644)
    os.replace(tmp, p)


def _read_json(p: Path):
    try:
        return json.loads(Path(p).read_text())
    except (OSError, ValueError):
        return None


def tree_sha256(folder) -> str:
    """Content hash of a release folder (paths + bytes), ignoring caches and ATTa's own markers. Two copies of
    the same code hash the same, wherever they live."""
    folder = Path(folder)
    h = hashlib.sha256()
    for p in sorted(folder.rglob("*")):
        rel = p.relative_to(folder)
        if "__pycache__" in rel.parts or p.name in _MARKERS or p.suffix == ".pyc":
            continue
        if p.is_symlink():
            h.update(b"L" + str(rel).encode() + b"\0" + os.readlink(p).encode() + b"\0")
        elif p.is_file():
            h.update(b"F" + str(rel).encode() + b"\0")
            with open(p, "rb") as f:
                for chunk in iter(lambda: f.read(1 << 20), b""):
                    h.update(chunk)
            h.update(b"\0")
    return h.hexdigest()


def live(app) -> Path | None:
    """The release folder APP points at right now (resolved), or None."""
    app = Path(app)
    if not app.is_symlink():
        return app.resolve() if app.is_dir() else None
    try:
        return app.resolve(strict=True)
    except OSError:
        return None


def version_of(release) -> str:
    rj = _read_json(Path(release) / "release.json") or {}
    if rj.get("version"):
        return str(rj["version"])
    return Path(release).name.split("-")[0] or "unknown"


def is_verified(release) -> bool:
    r = Path(release)
    return (r / VERIFIED).is_file() and not (r / FAILED).exists()


def known_good(rels):
    """The known-good record, but only while its folder still exists and is still verified."""
    doc = _read_json(Path(rels) / KNOWN_GOOD)
    if not doc or not doc.get("release") or not is_verified(doc["release"]):
        return None
    return doc


def previous_known_good(rels):
    doc = _read_json(Path(rels) / PREVIOUS_KNOWN_GOOD)
    if not doc or not doc.get("release") or not is_verified(doc["release"]):
        return None
    return doc


def _same(a, b) -> bool:
    try:
        return Path(a).resolve() == Path(b).resolve()
    except OSError:
        return False


def mark_verified(app, release, deployment_id, *, adopted=False):
    """Record that `release` passed every check while live. Refuses if it isn't the live one or was marked failed."""
    release = Path(release).resolve()
    if not release.is_dir():
        raise ReleaseError(f"{release} is not a release folder")
    if (release / FAILED).exists():
        raise ReleaseError(f"{release.name} was marked failed; it can never be verified")
    cur = live(app)
    if cur is None or not _same(cur, release):
        raise ReleaseError(f"{release.name} is not the live release ({cur.name if cur else 'nothing'} is); "
                           "only what actually served the checks can be verified")
    doc = {"release": str(release), "version": version_of(release), "deployment_id": str(deployment_id),
           "verified_at": _now(), "adopted": bool(adopted), "code_sha256": tree_sha256(release)}
    _write_json(release / VERIFIED, doc)
    return doc


def mark_failed(release, deployment_id, reason):
    release = Path(release)
    if not release.is_dir():
        return None
    (release / VERIFIED).unlink(missing_ok=True)
    doc = {"release": str(release), "deployment_id": str(deployment_id), "failed_at": _now(), "reason": str(reason)[:500]}
    _write_json(release / FAILED, doc)
    return doc


def promote(rels, app, release, deployment_id):
    """Make `release` the known-good (the one before it becomes previous-known-good). All checks or nothing."""
    rels, release = Path(rels), Path(release).resolve()
    v = _read_json(release / VERIFIED)
    if not v or not is_verified(release):
        raise ReleaseError(f"{release.name} is not verified; only verified releases can become known-good")
    if v.get("deployment_id") != str(deployment_id):
        raise ReleaseError(f"{release.name} was verified by deployment {v.get('deployment_id')}, not {deployment_id}")
    cur = live(app)
    if cur is None or not _same(cur, release):
        raise ReleaseError(f"{release.name} is not live; refusing to record it as known-good")
    old = known_good(rels)
    if old and not _same(old["release"], release):
        _write_json(rels / PREVIOUS_KNOWN_GOOD, old)
    doc = {**v, "promoted_at": _now()}
    _write_json(rels / KNOWN_GOOD, doc)
    return doc


def adopt(rels, app, deployment_id):
    """First v117 deploy on an older server: the healthy live release becomes known-good (marked adopted).
    The caller must have checked its health first. No-op (returns the record) if a known-good already exists."""
    kg = known_good(rels)
    if kg:
        return kg
    cur = live(app)
    if cur is None:
        return None
    mark_verified(app, cur, deployment_id, adopted=True)
    return promote(rels, app, cur, deployment_id)


def switch(app, release):
    """Point APP at `release` with one atomic rename (never a moment with no APP)."""
    app, release = Path(app), Path(release).resolve()
    if not release.is_dir():
        raise ReleaseError(f"{release} is not a release folder")
    tmp = app.with_name(app.name + f".next.{os.getpid()}")
    tmp.unlink(missing_ok=True)
    os.symlink(str(release), tmp)
    os.replace(tmp, app)


def prune(rels, app, keep=3, *, protect=()):
    """Delete failed releases and old ones. Never: the live, known-good, previous-known-good release, or any in
    `protect`. Of the rest, verified releases are kept (newest first) up to `keep`; unverified ones — a failed,
    interrupted or never-checked deploy — are removed (they can't be rollback targets anyway)."""
    rels = Path(rels)
    if not rels.is_dir():
        return []
    safe = {p.resolve() for p in [live(app)] if p}
    for rec in (known_good(rels), previous_known_good(rels)):
        if rec:
            safe.add(Path(rec["release"]).resolve())
    safe |= {Path(p).resolve() for p in protect}
    removed, verified_kept = [], 0
    dirs = sorted((d for d in rels.iterdir() if d.is_dir() and not d.is_symlink()),
                  key=lambda d: d.stat().st_mtime, reverse=True)
    for d in dirs:
        r = d.resolve()
        if r in safe:
            continue
        if d.name.startswith((".txn", ".incoming-")):
            # scratch: transaction snapshots are pruned by their own owner; stale incoming copies go
            if d.name.startswith(".incoming-"):
                shutil.rmtree(d, ignore_errors=True); removed.append(str(d))
            continue
        if is_verified(d) and verified_kept < keep:
            verified_kept += 1
            continue
        shutil.rmtree(d, ignore_errors=True)
        removed.append(str(d))
    return removed


def main(argv):
    """CLI for bootstrap.sh:  releases.py known-good RELS | verify APP REL ID | promote RELS APP REL ID |
    adopt RELS APP ID | failed REL ID REASON | switch APP REL | prune RELS APP KEEP [PROTECT...] | sha REL"""
    try:
        cmd, a = argv[0], argv[1:]
        if cmd == "known-good":
            kg = known_good(a[0]); print(kg["release"] if kg else ""); return 0 if kg else 3
        if cmd == "verify":
            print(json.dumps(mark_verified(a[0], a[1], a[2]))); return 0
        if cmd == "promote":
            print(json.dumps(promote(a[0], a[1], a[2], a[3]))); return 0
        if cmd == "adopt":
            d = adopt(a[0], a[1], a[2]); print(d["release"] if d else ""); return 0 if d else 3
        if cmd == "failed":
            mark_failed(a[0], a[1], a[2]); return 0
        if cmd == "switch":
            switch(a[0], a[1]); return 0
        if cmd == "prune":
            for r in prune(a[0], a[1], int(a[2]), protect=a[3:]):
                print(f"removed {r}")
            return 0
        if cmd == "sha":
            print(tree_sha256(a[0])); return 0
    except (IndexError, ValueError):
        pass
    except ReleaseError as e:
        print(f"releases: {e}", file=sys.stderr); return 1
    print(main.__doc__, file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
