"""Who may deploy. v115: decided by where a job CAME FROM, never by the name on it.

Every queued job carries an `origin`:
  "local"  deployctl (root) or a zip in ADM's incoming/ folder, or a bundle the pipeline took from its own
           root-only local inbox. Trusted only if the job's meta file and the queue folder belong to this
           daemon's user and nobody else can write them, AND requested_by is the matching internal name.
  "web"    a bundle uploaded on the web Add page. Runs only if `requested_by` is an enabled admin in the
           live accounts file right now, and is not a reserved internal name (reserved.py).
No origin, an unknown origin, or an unreadable accounts file -> refused. Nothing lives in the queue
without being written by root, so a web user can't write "local" into a job; the protection check catches
a queue folder someone loosened by hand."""
import importlib.util, json, os, stat
from pathlib import Path
from . import config

USERS_FILE = config.ROOT / "state" / "users.json"
# Internal requester names per local entry point. A local job naming anything else is refused.
LOCAL_REQUESTERS = {"deployctl": "deployctl", "incoming": "incoming", "system": "pipeline local inbox"}
_RESERVED_FILE = Path(__file__).resolve().parents[2] / "reserved.py"


def _reserved():
    """reserved.py from the code folder ADM ships in (04-deployment/ in the tree, /opt/app-builder live).
    Missing or broken -> None, and every web job is refused (fail closed)."""
    try:
        spec = importlib.util.spec_from_file_location("atta_reserved", _RESERVED_FILE)
        mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
        return mod
    except Exception:
        return None


def protected(path) -> tuple:
    """(ok, why): owned by this process's user, not a symlink, not writable by group or others."""
    try:
        s = os.lstat(path)
    except OSError as e:
        return False, f"{path}: {e}"
    if stat.S_ISLNK(s.st_mode):
        return False, f"{path} is a symlink"
    if s.st_uid != os.geteuid():
        return False, f"{path} is owned by uid {s.st_uid}, not {os.geteuid()}"
    if s.st_mode & 0o022:
        return False, f"{path} is writable by group/others (mode {oct(s.st_mode & 0o777)})"
    return True, ""


def allowed(meta):
    """(ok, reason) for one queued job's meta dict."""
    if not isinstance(meta, dict):
        return False, "job has no metadata"
    origin, who = meta.get("origin"), meta.get("requested_by")
    res = _reserved()
    if res is None:
        return False, f"reserved-name list {_RESERVED_FILE} unreadable; refusing"
    if origin not in res.ORIGINS:
        return False, f"job has no recognised origin ({origin!r}); only 'web' and 'local' jobs run"
    job = str(meta.get("job_id") or "")
    for p in (config.QUEUE, config.QUEUE / f"{job}.json"):
        ok, why = protected(p)
        if not ok:
            return False, f"queue not protected: {why}"
    if origin == res.ORIGIN_LOCAL:
        if who in LOCAL_REQUESTERS:
            return True, f"local ({LOCAL_REQUESTERS[who]})"
        return False, f"local job names {who!r}, which is not a local entry point"
    # web
    if not isinstance(who, str) or not who or res.is_reserved(who):
        return False, f"web job names {who!r}, a reserved or empty name; refusing"
    try:
        u = json.loads(USERS_FILE.read_text()).get("users", {}).get(who)
    except (OSError, ValueError) as e:
        return False, f"accounts file unreadable ({e}); refusing a web-queued deploy"
    if not u:
        return False, f"requested by unknown account {who!r}"
    if u.get("disabled"):
        return False, f"requested by disabled account {who!r}"
    if u.get("role") != "admin":
        return False, f"requested by {who!r}, who is not an admin"
    return True, "admin"
