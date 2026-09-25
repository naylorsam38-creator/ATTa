"""The deploy queue: a zip plus a small meta file per job, processed oldest first."""
from pathlib import Path
import json, shutil
from . import config, journal


def enqueue(archive, build_id=None, original_name=None, requested_by="deployctl"):
    """Copy the bundle into the queue and open its journal. Returns the job id."""
    config.ensure_dirs()
    a = Path(archive).resolve()
    if not a.is_file():
        raise FileNotFoundError(f"bundle not found: {a}")
    job = journal.new_job_id()
    dest = config.QUEUE / f"{job}.zip"
    shutil.copy2(a, dest)
    meta = {"job_id": job, "archive": str(dest), "original_name": original_name or a.name,
            "build_id": build_id, "requested_by": requested_by}
    (config.QUEUE / f"{job}.json").write_text(json.dumps(meta, indent=2) + "\n")
    journal.record(job, "QUEUED", build_id=build_id, original_name=meta["original_name"], requested_by=requested_by,
                   archive=str(dest))
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
    """Anything dropped into INCOMING becomes a queued job (the file is moved out of INCOMING)."""
    jobs = []
    for z in sorted(config.INCOMING.glob("*.zip")):
        job = enqueue(z, original_name=z.name, requested_by="incoming")
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
