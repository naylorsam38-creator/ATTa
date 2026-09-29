#!/usr/bin/env python3
"""
builds.py — one record per build, owned by the account that started it.

A build is created when a logged-in user uploads a bundle. The gateway writes the record,
the pipeline moves it through its states, and the Coolify hand-off reads it.

States, in order (any step can end in FAILED or NOT_QUALIFIED instead):

  QUEUED -> VALIDATING -> FETCHING_LIBRARY -> INSTALLING_UI_CAPABILITY
         -> READY_FOR_WATCHER -> QUALIFYING -> QUALIFIED

QUALIFIED is set only when every app target passed all six watcher stages, including the
real-browser stage 6 (status OK). "PASS (CLEAN not run)" is not qualified. Only a
QUALIFIED build is ever handed to Coolify (see coolify_handoff.py).
"""
from __future__ import annotations
import json, os, re, secrets, tempfile, time
from pathlib import Path

# ===================== CONFIG — edit here, nothing below needs reading =====================
# Where the app keeps its data. Same variable the gateway and pipeline use.
ROOT = Path(os.environ.get("APP_BUILDER_ROOT", "/srv/app-builder"))
# One JSON file per build lives here.
BUILDS_DIR = ROOT / "state" / "builds"
# How many state changes to keep in each build's history.
HISTORY_LIMIT = 50
# ==========================================================================================

QUEUED = "QUEUED"
READY_FOR_WATCHER = "READY_FOR_WATCHER"
QUALIFYING = "QUALIFYING"
QUALIFIED = "QUALIFIED"
NOT_QUALIFIED = "NOT_QUALIFIED"
# Some apps passed every stage (they go to Coolify); the rest are healed or reported.
PARTIALLY_QUALIFIED = "PARTIALLY_QUALIFIED"
# A bundle upload with no apps in the library yet: the system is updated and ready for apps.
PACKAGE_INSTALLED = "PACKAGE_INSTALLED"
FAILED = "FAILED"

ID_RE = re.compile(r"^b-\d{8}-\d{6}-[0-9a-f]{8}$")


def new_id() -> str:
    return time.strftime("b-%Y%m%d-%H%M%S-", time.gmtime()) + secrets.token_hex(4)


def path(build_id: str) -> Path:
    if not ID_RE.match(build_id):
        raise ValueError(f"bad build id {build_id!r}")
    return BUILDS_DIR / f"{build_id}.json"


def _write(rec: dict) -> None:
    BUILDS_DIR.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=BUILDS_DIR, prefix=".build.")
    with os.fdopen(fd, "w") as f:
        json.dump(rec, f, indent=2); f.write("\n")
    os.replace(tmp, path(rec["id"]))


def create(owner: str, **fields) -> dict:
    now = time.time()
    rec = {"id": new_id(), "owner": owner, "state": QUEUED, "created": now, "updated": now,
           "history": [{"state": QUEUED, "at": now}], **fields}
    _write(rec)
    return rec


def get(build_id: str) -> dict | None:
    try:
        return json.loads(path(build_id).read_text())
    except (FileNotFoundError, ValueError, json.JSONDecodeError):
        return None


def update(build_id: str, state: str | None = None, **fields) -> dict:
    """Merge fields into the record; a new state is also appended to its history.
    Only the pipeline process writes after creation, so there is one writer at a time."""
    rec = get(build_id)
    if rec is None:
        raise ValueError(f"no build {build_id!r}")
    now = time.time()
    rec.update(fields, updated=now)
    if state and state != rec.get("state"):
        rec["state"] = state
        entry = {"state": state, "at": now}
        if fields.get("error"):
            entry["error"] = str(fields["error"])[:500]
        rec["history"] = (rec.get("history") or [])[-(HISTORY_LIMIT - 1):] + [entry]
    _write(rec)
    return rec


def all_builds() -> list[dict]:
    out = []
    for p in BUILDS_DIR.glob("b-*.json"):
        try:
            out.append(json.loads(p.read_text()))
        except (OSError, json.JSONDecodeError):
            continue
    return sorted(out, key=lambda r: r.get("created", 0), reverse=True)


def visible_to(account: dict) -> list[dict]:
    """Admins see every build; everyone else sees only their own."""
    rows = all_builds()
    if account.get("role") == "admin":
        return rows
    return [r for r in rows if r.get("owner") == account.get("name")]


def can_see(account: dict, rec: dict) -> bool:
    return account.get("role") == "admin" or rec.get("owner") == account.get("name")
