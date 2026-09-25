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
    `protect`. Verified releases are kept, newest first, until `keep` verified ones are on disk (protected ones
    count); unverified ones — a failed, interrupted or never-checked deploy — are removed (they can't be rollback
    targets anyway)."""
    rels = Path(rels)
    if not rels.is_dir():
        return []
    safe = {p.resolve() for p in [live(app)] if p}
    for rec in (known_good(rels), previous_known_good(rels)):
        if rec:
            safe.add(Path(rec["release"]).resolve())
    safe |= {Path(p).resolve() for p in protect}
    # `keep` is the total of verified releases on disk; the protected ones count toward it (and stay regardless).
    removed, verified_kept = [], sum(1 for p in safe if p.is_dir() and is_verified(p))
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


def stage(src, rels):
    """Copy the code in `src` (a bundle's 04-deployment, or a release folder) into a NEW release folder under `rels`,
    check it, and return that folder. Nothing live changes: APP is switched later, and only after everything passed.

      - regular files and folders only: symlinks are not copied (noted), caches never;
      - the bundle's release.json goes in beside the code (the gateway reports it; checks use its features);
      - every .py must compile (checked in memory: nothing written); otherwise the folder is removed and it fails;
      - readable by the unprivileged service users (v116 runs the gateway as atta-web, which could not read a
        0700 release folder made by mktemp -d), writable by root only.
    Built in a hidden .incoming-* folder and renamed into place, so a half-copied release never exists by name."""
    import secrets, shutil, stat, time
    src, rels = Path(src).resolve(), Path(rels)
    if not src.is_dir():
        raise ReleaseError(f"{src} is not a folder")
    rj = next((p for p in (src.parent / "release.json", src / "release.json") if p.is_file()), None)
    ver = str((_read_json(rj) or {}).get("version") or "unknown") if rj else "unknown"
    ver = "".join(c if c.isalnum() or c in "._-" else "_" for c in ver)[:40] or "unknown"
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    rels.mkdir(parents=True, exist_ok=True)
    os.chmod(rels, 0o755)
    for old in rels.glob(".incoming-*"):                 # leftovers of an interrupted run (older than an hour)
        if time.time() - old.stat().st_mtime > 3600:
            shutil.rmtree(old, ignore_errors=True)
    tmp = rels / f".incoming-{ver}-{stamp}-{secrets.token_hex(4)}"
    tmp.mkdir(mode=0o755)
    skipped = []
    try:
        for dirpath, dirnames, filenames in os.walk(src):
            d = Path(dirpath)
            rel = d.relative_to(src)
            dirnames[:] = [n for n in dirnames if n != "__pycache__" and not (d / n).is_symlink()]
            skipped += [str(rel / n) for n in os.listdir(d) if (d / n).is_symlink()]
            (tmp / rel).mkdir(mode=0o755, exist_ok=True)
            for n in filenames:
                f = d / n
                if f.is_symlink() or n.endswith(".pyc") or not f.is_file():
                    continue
                dst = tmp / rel / n
                shutil.copyfile(f, dst)
                os.chmod(dst, 0o755 if f.stat().st_mode & stat.S_IXUSR else 0o644)
        if rj is not None and not (tmp / "release.json").exists():
            shutil.copyfile(rj, tmp / "release.json")
            os.chmod(tmp / "release.json", 0o644)
        bad = []
        for py in sorted(tmp.rglob("*.py")):
            try:
                compile(py.read_bytes(), str(py.relative_to(tmp)), "exec")
            except (SyntaxError, ValueError) as e:
                bad.append(f"{py.relative_to(tmp)} ({e.__class__.__name__}: line {getattr(e, 'lineno', '?')})")
        if bad:
            raise ReleaseError("python does not compile in the new release: " + ", ".join(bad))
        final = rels / f"{ver}-{stamp}"
        n = 1
        while final.exists():
            n += 1
            final = rels / f"{ver}-{stamp}-{n}"
        os.rename(tmp, final)
    except BaseException:
        shutil.rmtree(tmp, ignore_errors=True)
        raise
    if skipped:
        print(f"note: symlinks were not copied into the release: {', '.join(sorted(set(skipped))[:10])}", file=sys.stderr)
    return final


def migrate_legacy(app, rels):
    """A server laid out before v114.1 has APP as a real folder: it becomes a release of its own and APP a symlink
    to it (same code, same inodes: nothing running notices). Returns the new release path, or None."""
    import time
    app, rels = Path(app), Path(rels)
    if app.is_symlink() or not app.is_dir():
        return None
    rels.mkdir(parents=True, exist_ok=True)
    dest = rels / f"legacy-{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())}"
    os.rename(app, dest)
    switch(app, dest)
    return dest


def main(argv):
    """CLI for bootstrap.sh:  releases.py known-good RELS | verify APP REL ID | promote RELS APP REL ID |
    adopt RELS APP ID | failed REL ID REASON | switch APP REL | prune RELS APP KEEP [PROTECT...] | sha REL |
    stage SRC RELS | migrate-legacy APP RELS"""
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
        if cmd == "stage":
            print(stage(a[0], a[1])); return 0
        if cmd == "migrate-legacy":
            d = migrate_legacy(a[0], a[1]); print(d or ""); return 0
    except (IndexError, ValueError):
        pass
    except ReleaseError as e:
        print(f"releases: {e}", file=sys.stderr); return 1
    print(main.__doc__, file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
