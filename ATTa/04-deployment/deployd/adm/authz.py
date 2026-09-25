"""Who may deploy (v115).

A job's authority comes from WHERE it came from, never from a name in it:

  origin "local"  queued on the server by root: `deployctl deploy`, a zip in <ADM>/incoming/, or the pipeline
                  for a zip root placed in its inbox. Trusted because only root can write the queue.
  origin "web"    queued by the pipeline for a bundle uploaded on the web. Runs only if the account that
                  uploaded it is an enabled admin in the live accounts file.

Every job, whatever its origin, must sit in a queue folder only TRUSTED_UID can write, as a regular
root-owned file with a single link: anyone able to write the queue could otherwise forge any job. A job
with no origin, an unknown origin, or a queue that fails those checks is refused. A reserved name
(system, incoming, deployctl, local, root) never counts as an account."""
import json, os, stat
from pathlib import Path
from . import config

VALID_ORIGINS = ("web", "local")
# Same set as RESERVED_EXACT_NAMES in 04-deployment/accounts.py (this package cannot import it; a test keeps them equal).
RESERVED_EXACT_NAMES = frozenset({"system", "incoming", "deployctl", "local", "root"})
USERS_FILE = config.ROOT / "state" / "users.json"


def secure_dir(d, uid=None):
    """(ok, reason): d is a real folder (not a symlink) owned by uid that nobody else can write."""
    uid = config.TRUSTED_UID if uid is None else uid
    try:
        st = os.lstat(d)
    except OSError as e:
        return False, f"cannot check folder {d}: {e}"
    if not stat.S_ISDIR(st.st_mode):
        return False, f"{d} is not a real folder"
    if st.st_uid != uid:
        return False, f"{d} is owned by uid {st.st_uid}, not {uid}"
    if st.st_mode & 0o022:
        return False, f"{d} is writable by group or others (mode {oct(st.st_mode & 0o777)})"
    return True, ""


def secure_file(f, d, uid=None, private_dir=False):
    """(ok, reason): f is a regular file directly inside d, owned by uid, not writable by others, one link;
    and d passes secure_dir (and, if private_dir, is not even readable by others)."""
    uid = config.TRUSTED_UID if uid is None else uid
    ok, why = secure_dir(d, uid)
    if not ok:
        return False, why
    try:
        if private_dir and os.lstat(d).st_mode & 0o077:
            return False, f"{d} must be private to its owner (mode 0700)"
        if Path(os.path.abspath(f)).parent != Path(os.path.abspath(d)):
            return False, f"{f} is not directly inside {d}"
        st = os.lstat(f)
    except OSError as e:
        return False, f"cannot check {f}: {e}"
    if not stat.S_ISREG(st.st_mode):
        return False, f"{f} is not a regular file"
    if st.st_uid != uid:
        return False, f"{f} is owned by uid {st.st_uid}, not {uid}"
    if st.st_mode & 0o022:
        return False, f"{f} is writable by group or others"
    if st.st_nlink != 1:
        return False, f"{f} has {st.st_nlink} hard links"
    return True, ""


def _admin(requested_by):
    name = str(requested_by or "").strip().lower()
    if not name:
        return False, "web job names no account"
    if name in RESERVED_EXACT_NAMES:
        return False, f"{requested_by!r} is a reserved name, not an account"
    try:
        u = json.loads(USERS_FILE.read_text()).get("users", {}).get(requested_by)
    except (OSError, ValueError) as e:
        return False, f"accounts file unreadable ({e}); refusing a web-queued deploy"
    if not u:
        return False, f"requested by unknown account {requested_by!r}"
    if u.get("disabled"):
        return False, f"requested by disabled account {requested_by!r}"
    if u.get("role") != "admin":
        return False, f"requested by {requested_by!r}, who is not an admin"
    return True, "admin"


def allowed(meta):
    """(ok, reason) for one queued job's meta dict. Fails closed on anything it cannot verify."""
    if not isinstance(meta, dict):
        return False, "job metadata is not an object"
    origin = meta.get("origin")
    if origin not in VALID_ORIGINS:
        return False, f"missing or unknown job origin {origin!r} (a job queued by an older ATTa must be queued again)"
    job = str(meta.get("job_id") or "")
    if not job or "/" in job or job.startswith("."):
        return False, "job has no usable id"
    # v117: a job is checked where it is when it runs: claimed (RUNNING), or still queued (older callers).
    entry_dir = config.RUNNING if (config.RUNNING / f"{job}.json").exists() else config.QUEUE
    ok, why = secure_file(entry_dir / f"{job}.json", entry_dir, private_dir=True)
    if not ok:
        return False, f"queue entry not trusted: {why}"
    ok, why = secure_file(meta.get("archive") or "", config.QUEUE, private_dir=True)
    if not ok:
        return False, f"queued bundle not trusted: {why}"
    if origin == "local":
        return True, "local"
    return _admin(meta.get("requested_by"))
