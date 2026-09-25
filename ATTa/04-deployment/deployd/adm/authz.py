"""Who may deploy. v114: a job queued from a web upload names the account that uploaded it; ADM runs
it only if that account is an enabled admin in the live accounts file. Jobs from the server itself
(deployctl, a zip in incoming/, a zip placed in the pipeline inbox) are trusted: they need root."""
import json
from . import config

LOCAL_REQUESTERS = ("deployctl", "incoming", "system")
USERS_FILE = config.ROOT / "state" / "users.json"


def allowed(requested_by):
    """(ok, reason)."""
    if requested_by in LOCAL_REQUESTERS:
        return True, "local"
    try:
        u = json.loads(USERS_FILE.read_text()).get("users", {}).get(requested_by or "")
    except (OSError, ValueError) as e:
        return False, f"accounts file unreadable ({e}); refusing a web-queued deploy"
    if not u:
        return False, f"requested by unknown account {requested_by!r}"
    if u.get("disabled"):
        return False, f"requested by disabled account {requested_by!r}"
    if u.get("role") != "admin":
        return False, f"requested by {requested_by!r}, who is not an admin"
    return True, "admin"
