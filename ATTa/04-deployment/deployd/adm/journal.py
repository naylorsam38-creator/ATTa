"""One JSON file per deploy job. Every event is appended; the file is rewritten atomically.
Read a job's verdict with:  deployctl journal <job>   (last event tells you what happened).

v117: the journal doc is also the DEPLOYMENT RECORD (adm/deployment.py: state machine, previous/attempted/final
live version, pid/pgid, rollback result). Writers are ADM and bootstrap.sh (a separate process, for manual
`bash run`s and the steps it performs under ADM), so every change is a locked read-modify-write (update())."""
from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl, json, os, re, threading, uuid
from . import config

_LOCK = threading.Lock()
VERDICTS = ("QUEUED", "STAGING", "CHECKED", "BACKED_UP", "ACTIVATING", "HEALTHY", "DEPLOYED",
            "FAILED", "ROLLING_BACK", "ROLLED_BACK", "ROLLBACK_FAILED")
ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,99}")


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def new_job_id(prefix=""):
    return prefix + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8]


def path(job):
    if not ID_RE.fullmatch(str(job)):
        raise ValueError(f"not a job id: {str(job)[:40]!r}")
    return config.JOURNAL / f"{job}.json"


def load(job):
    p = path(job)
    if p.exists():
        return json.loads(p.read_text())
    return {"job_id": job, "created_at": now(), "verdict": "QUEUED", "events": []}


def exists(job):
    return path(job).exists()


def _write(doc):
    config.JOURNAL.mkdir(parents=True, exist_ok=True)
    p = path(doc["job_id"]); tmp = p.with_name(p.name + f".{os.getpid()}.tmp")
    tmp.write_text(json.dumps(doc, indent=2) + "\n")
    os.replace(tmp, p)


@contextmanager
def _file_lock():
    config.JOURNAL.mkdir(parents=True, exist_ok=True)
    fd = os.open(config.JOURNAL / ".lock", os.O_RDWR | os.O_CREAT | os.O_CLOEXEC, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


def update(job, fn):
    """Locked read-modify-write of one job's doc: fn(doc) changes it in place (and may raise to abort)."""
    with _LOCK, _file_lock():
        doc = load(job)
        fn(doc)
        doc["updated_at"] = now()
        _write(doc)
        return doc


def record(job, event, **fields):
    """Append one event. If `event` is a verdict word it also becomes the job's verdict — unless the job is a
    v117 deployment record, whose verdict only the state machine (adm/deployment.py) sets."""
    def apply(doc):
        doc["events"].append({"at": now(), "event": event, **fields})
        if event in VERDICTS and "state" not in doc:
            doc["verdict"] = event
        for k in ("build_id", "version", "release", "backup", "log", "original_name", "requested_by", "origin"):
            if k in fields and fields[k] is not None:
                doc[k] = fields[k]
    return update(job, apply)


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
