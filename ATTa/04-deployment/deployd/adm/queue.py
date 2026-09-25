"""The deploy queue: a zip plus a small meta file per job, processed oldest first."""
from pathlib import Path
import json, os, shutil, stat
from . import authz, config, journal


def enqueue(archive, *, origin, requested_by, build_id=None, original_name=None, claimed=False, job_id=None):
    """Copy the bundle into the queue and open its journal. Returns the job id.
    origin: "local" (queued on the server by root) or "web" (uploaded on the web by `requested_by`).
    Both are required: authz.py decides from them, and a job without them is refused.
    claimed=True (v117, deployctl): the caller already holds the deploy lock and runs the job itself, so it goes
    straight to RUNNING and deployd never sees it as pending (no second process can pick it up)."""
    if origin not in authz.VALID_ORIGINS:
        raise ValueError(f"origin must be one of {authz.VALID_ORIGINS}, not {origin!r}")
    if origin == "web" and not str(requested_by or "").strip():
        raise ValueError("a web job must name the account that uploaded it")
    config.ensure_dirs()
    a = Path(archive).resolve()
    if not a.is_file():
        raise FileNotFoundError(f"bundle not found: {a}")
    job = job_id or journal.new_job_id()
    if not journal.ID_RE.fullmatch(job) or journal.exists(job):
        raise ValueError(f"job id {job!r} is not usable (malformed, or already used)")
    dest = config.QUEUE / f"{job}.zip"
    shutil.copy2(a, dest)
    dest.chmod(0o600)
    meta = {"job_id": job, "archive": str(dest), "original_name": original_name or a.name,
            "build_id": build_id, "origin": origin, "requested_by": requested_by}
    mp = (config.RUNNING if claimed else config.QUEUE) / f"{job}.json"
    fd = os.open(mp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(json.dumps(meta, indent=2) + "\n")
    journal.record(job, "QUEUED", build_id=build_id, original_name=meta["original_name"], requested_by=requested_by,
                   origin=origin, archive=str(dest))
    return job


def claim(meta):
    """v117: take one queued job for this process, atomically (rename QUEUE/<job>.json -> RUNNING/<job>.json).
    Exactly one caller wins; the others get None. Call only while holding the deploy lock."""
    job = str(meta.get("job_id") or "")
    if not journal.ID_RE.fullmatch(job):
        return None
    src, dst = config.QUEUE / f"{job}.json", config.RUNNING / f"{job}.json"
    try:
        os.rename(src, dst)          # atomic on one filesystem; fails for everyone but the first
    except FileNotFoundError:
        return None
    try:
        return json.loads(dst.read_text())
    except (OSError, ValueError):
        return None


def claim_next():
    """Claim the oldest queued job, or None."""
    for meta in pending():
        m = claim(meta)
        if m is not None:
            return m
    return None


def running():
    """Claimed jobs (RUNNING). While the deploy lock is free, every one of these is a deploy that was killed."""
    out = []
    for m in sorted(config.RUNNING.glob("*.json")):
        try:
            out.append(json.loads(m.read_text()))
        except (OSError, ValueError):
            out.append({"job_id": m.stem, "unreadable": True})
    return out


def pending():
    """Queued jobs, oldest first."""
    out = []
    for m in sorted(config.QUEUE.glob("*.json")):
        try:
            d = json.loads(m.read_text())
        except (OSError, ValueError):
            continue
        if Path(d.get("archive", "")).is_file():
            out.append(d)
    return out


def sweep_incoming():
    """Anything dropped into INCOMING becomes a queued job (the file is moved out of INCOMING).
    v115: only while INCOMING is a folder nobody but TRUSTED_UID can write; otherwise nothing is taken
    from it (anyone who could write there could queue a root deploy)."""
    jobs = []
    ok, why = authz.secure_dir(config.INCOMING)
    if not ok:
        if any(config.INCOMING.glob("*.zip")):
            print(f"deployd: not taking bundles from {config.INCOMING}: {why}", flush=True)
        return jobs
    for z in sorted(config.INCOMING.glob("*.zip")):
        if z.is_symlink() or not z.is_file():
            continue
        job = enqueue(z, origin="local", requested_by="incoming", original_name=z.name)
        z.unlink(missing_ok=True)
        jobs.append(job)
    return jobs


def sweep_requests():
    """v116: bundles the pipeline (running as its own user) asks ADM to deploy. Each becomes a job with origin
    "web" (never "local", whoever owns the folder), so it runs only for an enabled admin. A request whose
    files are links, or not owned by the folder's owner, is discarded. Returns the new job ids."""
    jobs = []
    d = config.REQUESTS
    try:
        dst = os.lstat(d)
    except OSError:
        return jobs
    if not stat.S_ISDIR(dst.st_mode) or dst.st_mode & 0o022:
        print(f"deployd: not taking requests from {d}: not a private real folder", flush=True)
        return jobs
    for m in sorted(d.glob("*.json")):
        z = None
        try:
            ok = True
            for f in (m, d / (m.stem + ".zip")):
                st = os.lstat(f)
                ok = ok and stat.S_ISREG(st.st_mode) and st.st_uid == dst.st_uid and st.st_nlink == 1
            meta = json.loads(m.read_text()) if ok else {}
            z = d / (m.stem + ".zip")
            who = str(meta.get("requested_by") or "")
            if not ok or meta.get("archive") != z.name or not who:
                print(f"deployd: discarding malformed request {m.name}", flush=True)
            else:
                jobs.append(enqueue(z, origin="web", requested_by=who, build_id=meta.get("build_id"),
                                    original_name=meta.get("original_name")))
        except (OSError, ValueError) as e:
            print(f"deployd: discarding request {m.name}: {e}", flush=True)
        finally:
            m.unlink(missing_ok=True)
            if z is not None:
                z.unlink(missing_ok=True)
    return jobs


def finish(meta):
    """Remove a job from the queue once its journal holds the verdict (claimed or not)."""
    for p in (Path(meta.get("archive", "")), config.QUEUE / f"{meta['job_id']}.json",
              config.RUNNING / f"{meta['job_id']}.json"):
        try:
            p.unlink()
        except OSError:
            pass
