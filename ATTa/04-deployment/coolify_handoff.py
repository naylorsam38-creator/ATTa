#!/usr/bin/env python3
"""
coolify_handoff.py — hands QUALIFIED builds to Coolify. Nothing else reaches Coolify.

Contract (full write-up: docs/COOLIFY-HANDOFF.md):

  WHEN   a build's state becomes QUALIFIED. Every qualified build is handed off; there is no
         manual pick. A build in any other state is refused here (hand_off raises).
  WHAT   1. A hand-off manifest is written to  <ROOT>/state/coolify/outbox/<build_id>.json
            (build id, owner, qualified time, and each qualified app with its watcher verdict,
            ui_dir, target_url and proxy_url). This file is the record Coolify-side tooling reads.
         2. For each app in the manifest that has a Coolify resource UUID in
            <ROOT>/coolify_resources.json, the Coolify deploy API is called:
               POST {COOLIFY_URL}{COOLIFY_DEPLOY_PATH}?uuid=<resource uuid>&force=false
               Authorization: Bearer {COOLIFY_TOKEN}
            (checked against the supplied Coolify 4.3.23 source; accepted = a deployment_uuid came back)
  RESULT The build record gets a `coolify` block: status DISPATCHED once every app was accepted
         (a deployment was queued), otherwise RETRYING / BLOCKED_NOT_CONFIGURED / BLOCKED_UNMAPPED. Anything not yet
         dispatched is retried by the pipeline loop, so a qualified build is never dropped.
         An app already accepted is never deployed a second time for the same build.
"""
from __future__ import annotations
import json, os, time, tempfile
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

import builds

# ===================== CONFIG — edit here, nothing below needs reading =====================
ROOT = builds.ROOT
# Hand-off manifests land here, one per qualified build.
OUTBOX = ROOT / "state" / "coolify" / "outbox"
# App id -> Coolify resource UUID. The owner fills this in on the Coolify side of the boundary.
RESOURCES_FILE = ROOT / "coolify_resources.json"
# Base URL of the Coolify instance, e.g. https://coolify.example.com . Blank = not configured yet.
COOLIFY_URL = os.environ.get("COOLIFY_URL", "").rstrip("/")
# Coolify API token (Keys & Tokens -> API tokens, with deploy permission). Blank = not configured.
COOLIFY_TOKEN = os.environ.get("COOLIFY_TOKEN", "")
# Deploy endpoint path on the Coolify API. Change here if your Coolify version differs.
COOLIFY_DEPLOY_PATH = os.environ.get("COOLIFY_DEPLOY_PATH", "/api/v1/deploy")
# Seconds to wait for Coolify to answer one deploy call.
TIMEOUT = int(os.environ.get("COOLIFY_TIMEOUT", "30"))
# Retry spacing: first retry after this many seconds, doubling each time, capped at RETRY_MAX.
RETRY_BASE = int(os.environ.get("COOLIFY_RETRY_BASE", "30"))
RETRY_MAX = int(os.environ.get("COOLIFY_RETRY_MAX", "900"))
# ==========================================================================================

DISPATCHED = "DISPATCHED"
RETRYING = "RETRYING"
NOT_CONFIGURED = "BLOCKED_NOT_CONFIGURED"
UNMAPPED = "BLOCKED_UNMAPPED"


def _resources() -> dict:
    try:
        d = json.loads(RESOURCES_FILE.read_text())
        return d.get("apps", {}) if isinstance(d, dict) else {}
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def resource_for(app: str) -> dict | None:
    """v115: the Coolify resource for an app. coolify_resources.json maps an app to either a UUID string
    (an application) or {"uuid": "...", "kind": "application" | "service"}. None if unmapped/malformed."""
    r = _resources().get(app)
    if isinstance(r, str) and r.strip():
        return {"uuid": r.strip(), "kind": "application"}
    if isinstance(r, dict) and isinstance(r.get("uuid"), str) and r["uuid"].strip():
        kind = r.get("kind", "application")
        return {"uuid": r["uuid"].strip(), "kind": kind if kind in ("application", "service") else "application"}
    return None


def _write_manifest(m: dict) -> Path:
    OUTBOX.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=OUTBOX, prefix=".handoff.")
    with os.fdopen(fd, "w") as f:
        json.dump(m, f, indent=2); f.write("\n")
    dest = OUTBOX / f"{m['build_id']}.json"
    os.replace(tmp, dest)
    return dest


# A build reaches Coolify when all of its apps qualified, or some did (only those are sent).
HANDOFF_STATES = (builds.QUALIFIED, builds.PARTIALLY_QUALIFIED)


def hand_off(build_id: str, qualified_apps: list[dict]) -> dict:
    """Called by the pipeline the moment a build is QUALIFIED. Writes the manifest, then tries
    to dispatch. qualified_apps: [{app, verdict, ui_dir, target_url, proxy_url}, ...]"""
    rec = builds.get(build_id)
    if not rec or rec.get("state") not in HANDOFF_STATES:
        raise RuntimeError(f"refusing Coolify hand-off: build {build_id} is not QUALIFIED")
    # Only apps that passed every stage themselves; a partly qualified build sends just those.
    qualified_apps = [a for a in qualified_apps if a.get("qualified", True)]
    if not qualified_apps:
        raise RuntimeError(f"refusing Coolify hand-off: build {build_id} has no qualified app")
    manifest = {"schema": "APP_BUILDER_COOLIFY_HANDOFF.v1", "build_id": build_id, "owner": rec.get("owner"),
                "qualified_at": rec.get("qualified_at"), "apps": qualified_apps}
    _write_manifest(manifest)
    builds.update(build_id, coolify={"status": "QUEUED", "attempts": 0, "next_attempt_at": 0,
                                     "manifest": str(OUTBOX / f"{build_id}.json"),
                                     "apps": {a["app"]: {"status": "PENDING"} for a in qualified_apps}})
    return dispatch(build_id)


def _deploy(uuid: str) -> tuple[bool, str]:
    # Coolify 4.3.x (checked against the supplied source, DeployController::deploy): POST only
    # (a GET answers 405 "This endpoint has changed to a POST request"). A 200 only means a deploy
    # was queued if `deployments` has an entry for this uuid carrying a deployment_uuid.
    url = f"{COOLIFY_URL}{COOLIFY_DEPLOY_PATH}?{urlencode({'uuid': uuid, 'force': 'false'})}"
    req = Request(url, data=b"", method="POST",
                  headers={"Authorization": f"Bearer {COOLIFY_TOKEN}", "Accept": "application/json"})
    try:
        with urlopen(req, timeout=TIMEOUT) as r:
            body = r.read(20000).decode("utf-8", "replace")
            try:
                deps = json.loads(body).get("deployments") or []
            except (ValueError, AttributeError):
                deps = []
            mine = [d for d in deps if isinstance(d, dict) and d.get("resource_uuid") == uuid]
            if any(d.get("deployment_uuid") for d in mine):
                return True, f"HTTP {r.status}: queued {[d['deployment_uuid'] for d in mine if d.get('deployment_uuid')]}"
            return False, f"REJECTED: {(mine[0].get('message') if mine else body)[:1000]}"
    except HTTPError as e:
        return False, f"HTTP {e.code}: {e.read(2000).decode('utf-8', 'replace')}"
    except (URLError, OSError) as e:
        return False, f"UNREACHABLE: {e}"


def dispatch(build_id: str) -> dict:
    """Try every not-yet-accepted app of one build. Safe to call repeatedly."""
    rec = builds.get(build_id) or {}
    c = rec.get("coolify") or {}
    if rec.get("state") not in HANDOFF_STATES or c.get("status") == DISPATCHED:
        return c
    apps = c.get("apps", {})
    now = time.time()
    c["attempts"] = int(c.get("attempts", 0)) + 1
    c["last_attempt_at"] = now
    if not COOLIFY_URL or not COOLIFY_TOKEN:
        c["status"] = NOT_CONFIGURED
        c["last_error"] = "COOLIFY_URL and COOLIFY_TOKEN must be set in the .env file"
    else:
        for app, st in apps.items():
            if st.get("status") == "ACCEPTED":
                continue
            r = resource_for(app)
            uuid = r["uuid"] if r else None
            if not uuid:
                apps[app] = {"status": "UNMAPPED", "detail": f"no entry for {app!r} in {RESOURCES_FILE.name}"}
                continue
            ok, detail = _deploy(uuid)
            apps[app] = {"status": "ACCEPTED" if ok else "ERROR", "uuid": uuid, "detail": detail, "at": now}
        statuses = {s.get("status") for s in apps.values()}
        if statuses <= {"ACCEPTED"}:
            c["status"] = DISPATCHED; c["dispatched_at"] = now; c.pop("last_error", None)
        elif "ERROR" in statuses:
            c["status"] = RETRYING; c["last_error"] = "one or more Coolify deploy calls failed"
        else:
            c["status"] = UNMAPPED; c["last_error"] = f"apps missing from {RESOURCES_FILE.name}"
    if c["status"] != DISPATCHED:
        c["next_attempt_at"] = now + min(RETRY_MAX, RETRY_BASE * 2 ** min(c["attempts"] - 1, 10))
    c["apps"] = apps
    builds.update(build_id, coolify=c)
    return c


def dispatch_pending() -> None:
    """Called from the pipeline loop: retry every qualified build that hasn't fully reached Coolify."""
    now = time.time()
    for rec in builds.all_builds():
        if rec.get("state") not in HANDOFF_STATES:
            continue
        c = rec.get("coolify")
        try:
            if not c:  # qualified but the first hand-off never got written (e.g. disk error)
                hand_off(rec["id"], rec.get("qualification") or [])
            elif c.get("status") != DISPATCHED and now >= float(c.get("next_attempt_at", 0)):
                dispatch(rec["id"])
        except Exception as e:  # one bad record must not stop the others
            print(f"coolify dispatch {rec.get('id')}: {e}", flush=True)
