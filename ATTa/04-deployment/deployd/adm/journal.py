"""One JSON file per deploy job. Every event is appended; the file is rewritten atomically.
Read a job's verdict with:  deployctl journal <job>   (last event tells you what happened)."""
from datetime import datetime, timezone
import json, os, threading, uuid
from . import config

_LOCK = threading.Lock()
VERDICTS = ("QUEUED", "STAGING", "CHECKED", "BACKED_UP", "ACTIVATING", "HEALTHY", "DEPLOYED",
            "FAILED", "ROLLING_BACK", "ROLLED_BACK", "ROLLBACK_FAILED")


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def new_job_id():
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8]


def path(job):
    return config.JOURNAL / f"{job}.json"


def load(job):
    p = path(job)
    if p.exists():
        return json.loads(p.read_text())
    return {"job_id": job, "created_at": now(), "verdict": "QUEUED", "events": []}


def _write(doc):
    config.JOURNAL.mkdir(parents=True, exist_ok=True)
    p = path(doc["job_id"]); tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(doc, indent=2) + "\n")
    os.replace(tmp, p)


def record(job, event, **fields):
    """Append one event. If `event` is a verdict word it also becomes the job's verdict."""
    with _LOCK:
        doc = load(job)
        doc["events"].append({"at": now(), "event": event, **fields})
        if event in VERDICTS:
            doc["verdict"] = event
        for k in ("build_id", "version", "release", "backup", "log", "original_name", "requested_by", "origin"):
            if k in fields and fields[k] is not None:
                doc[k] = fields[k]
        doc["updated_at"] = now()
        _write(doc)
        return doc


def all_jobs():
    docs = []
    for p in sorted(config.JOURNAL.glob("*.json")):
        try:
            docs.append(json.loads(p.read_text()))
        except (OSError, ValueError):
            pass
    return docs


def for_build(build_id):
    """The job the pipeline queued for a given build record, or None."""
    for d in reversed(all_jobs()):
        if d.get("build_id") == build_id:
            return d
    return None
