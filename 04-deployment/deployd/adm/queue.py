"""The deploy queue: a zip plus a small meta file per job, processed oldest first."""
from pathlib import Path
import json, os, shutil
from . import config, journal, authz

ORIGINS = ("web", "local")


def enqueue(archive, build_id=None, original_name=None, requested_by=None, origin=None):
    """Copy the bundle into the queue and open its journal. Returns the job id.
    v115: `origin` ("web" or "local") and `requested_by` are required; there is no default identity."""
    if origin not in ORIGINS:
        raise ValueError(f"deploy job origin must be one of {ORIGINS}, not {origin!r}")
    if not isinstance(requested_by, str) or not requested_by:
        raise ValueError("deploy job needs requested_by")
    config.ensure_dirs()
    a = Path(archive).resolve()
    if not a.is_file():
        raise FileNotFoundError(f"bundle not found: {a}")
    job = journal.new_job_id()
    dest = config.QUEUE / f"{job}.zip"
    shutil.copy2(a, dest)
    os.chmod(dest, 0o600)
    meta = {"job_id": job, "archive": str(dest), "original_name": original_name or a.name,
            "build_id": build_id, "requested_by": requested_by, "origin": origin}
    mp = config.QUEUE / f"{job}.json"
    fd = os.open(mp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(json.dumps(meta, indent=2) + "\n")
    journal.record(job, "QUEUED", build_id=build_id, original_name=meta["original_name"], requested_by=requested_by,
                   origin=origin, archive=str(dest))
    return job


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
    """Anything dropped into INCOMING becomes a queued local job (the file is moved out of INCOMING).
    v115: only if INCOMING and the zip belong to this daemon's user and nobody else can write them.
    Anything else is moved to incoming/rejected/ and journaled as FAILED, never deployed."""
    jobs = []
    folder_ok, folder_why = authz.protected(config.INCOMING)
    for z in sorted(config.INCOMING.glob("*.zip")):
        ok, why = (folder_ok, folder_why) if not folder_ok else authz.protected(z)
        if ok and not z.is_file():
            ok, why = False, f"{z} is not a regular file"
        if not ok:
            rej = config.INCOMING / "rejected"
            rej.mkdir(mode=0o700, exist_ok=True)
            job = journal.new_job_id()
            journal.record(job, "FAILED", original_name=z.name, requested_by="incoming", origin="local",
                           reason=f"incoming zip refused: {why}", touched_live=False)
            try:
                os.replace(z, rej / f"{job}-{z.name}")
            except OSError:
                z.unlink(missing_ok=True)
            continue
        job = enqueue(z, original_name=z.name, requested_by="incoming", origin="local")
        z.unlink(missing_ok=True)
        jobs.append(job)
    return jobs


def finish(meta):
    """Remove a job from the queue once its journal holds the verdict."""
    for p in (Path(meta.get("archive", "")), config.QUEUE / f"{meta['job_id']}.json"):
        try:
            p.unlink()
        except OSError:
            pass
