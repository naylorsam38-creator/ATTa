"""Per-app Coolify deployment state, read from the build records coolify_handoff.py keeps, plus an optional
live probe of each dispatched app's URL.

Read-only toward Coolify: this never calls the deploy API, never provisions, never changes
coolify_resources.json. Dispatching stays coolify_handoff.py's alone, and so does its v120 rule that only a
fully QUALIFIED build is handed off (a PARTIALLY_QUALIFIED build's passing apps are reported here as
NOT_REQUESTED with that reason, not deployed).

What each state rests on:
  DISPATCHED     coolify_handoff recorded ACCEPTED: Coolify queued a deployment or started the service. It
                 does not mean the app came up.
  LIVE_HTTP_OK   probed by this module now (resilience.safe_http_probe): the URL Coolify was given for the
                 app answered HTTP with a status below 500. That is HTTP liveness, not the six-stage check.
  LIVE_FAILED    probed now: no TCP connection, a protocol/TLS mismatch, a timeout, or a 5xx.
  UNKNOWN        probing was asked for, but no URL is recorded for the dispatched app (or the hand-off
                 recorded a status this module does not know). Without --probe a dispatched app stays
                 DISPATCHED: that is what the records prove, and nothing more."""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any, Callable

from .models import DeploymentState
from .store import EventStore

BLOCKED_APP_STATES = {"UNMAPPED", "UNPROVISIONABLE"}
BLOCKED_BUILD_STATES = {"BLOCKED_NOT_CONFIGURED"}


def _root() -> Path:
    return Path(os.environ.get("APP_BUILDER_ROOT", "/srv/app-builder"))


def _builds(base: Path | None = None) -> list[dict]:
    d = (base or _root()) / "state" / "builds"
    out = []
    for p in sorted(d.glob("b-*.json")) if d.is_dir() else []:
        try:
            r = json.loads(p.read_text())
        except (OSError, ValueError):
            continue
        if isinstance(r, dict):
            out.append(r)
    return out


def _managed(base: Path | None = None) -> dict:
    try:
        d = json.loads(((base or _root()) / "coolify_resources.json").read_text())
    except (OSError, ValueError):
        return {}
    m = d.get("managed_by_atta") if isinstance(d, dict) else None
    return m if isinstance(m, dict) else {}


def _first_url(raw: Any) -> str | None:
    """Coolify keeps a service's domains as one comma-separated string; the first is the app's address."""
    if not isinstance(raw, str) or not raw.strip():
        return None
    u = raw.split(",")[0].strip()
    return u if u.startswith(("http://", "https://")) else None


def deployment_states(base: Path | None = None) -> dict[str, dict]:
    """app -> its deployment as the most recent build that tried to hand it off records it."""
    latest: dict[str, dict] = {}
    managed = _managed(base)
    for rec in sorted(_builds(base), key=lambda r: r.get("updated") or 0):
        c = rec.get("coolify") or {}
        for app, st in (c.get("apps") or {}).items():
            status = st.get("status")
            if c.get("status") in BLOCKED_BUILD_STATES:
                state, why = DeploymentState.BLOCKED, c.get("last_error") or c.get("status")
            elif status == "ACCEPTED":
                state, why = DeploymentState.DISPATCHED, st.get("detail")
            elif status == "PENDING":
                state, why = DeploymentState.PENDING, "hand-off written; deploy call not made yet"
            elif status in BLOCKED_APP_STATES:
                state, why = DeploymentState.BLOCKED, st.get("detail")
            elif status == "ERROR":
                state, why = DeploymentState.DISPATCH_ERROR, st.get("detail")
            else:
                state, why = DeploymentState.UNKNOWN, f"hand-off status {status!r}"
            latest[app] = {"app": app, "state": state.value, "why": (why or "")[:500], "build_id": rec.get("id"),
                           "build_state": rec.get("state"), "handoff_status": c.get("status"),
                           "uuid": st.get("uuid"), "url": _first_url(st.get("url")) or _first_url((managed.get(app) or {}).get("url")),
                           "dispatched_at": st.get("at") if status == "ACCEPTED" else None}
        # A build whose apps passed but which never reached the hand-off: say why instead of leaving a gap.
        if rec.get("state") == "PARTIALLY_QUALIFIED" and not c:
            for app in rec.get("qualified_apps") or []:
                latest.setdefault(app, {"app": app, "state": DeploymentState.NOT_REQUESTED.value,
                                        "why": "v120: a PARTIALLY_QUALIFIED build is not handed to Coolify",
                                        "build_id": rec.get("id"), "build_state": rec.get("state"),
                                        "handoff_status": None, "uuid": None, "url": None, "dispatched_at": None})
    return latest


def verify(probe: bool = False, base: Path | None = None, store: EventStore | None = None,
           prober: Callable[[str], dict] | None = None) -> dict:
    """Every app's deployment state; with probe=True, each DISPATCHED app with a URL is probed now. One app's
    probe failing never stops the others. Each verification is appended to run-events.jsonl."""
    if prober is None:
        from resilience import safe_http_probe
        prober = lambda url: safe_http_probe(url, timeout=10)   # noqa: E731
    rows = []
    for app, row in sorted(deployment_states(base).items()):
        row = dict(row)
        if row["state"] == DeploymentState.DISPATCHED.value:
            if not probe:
                row["live"] = "not probed (run with --probe to check the live URL)"
            elif not row.get("url"):
                row.update(state=DeploymentState.UNKNOWN.value, live="no URL recorded for the app")
            else:
                try:
                    obs = prober(row["url"])
                except Exception as e:
                    obs = {"status": "INCONCLUSIVE", "classification": "PROBE_INTERNAL_ERROR",
                           "error": f"{type(e).__name__}: {e}"}
                code = obs.get("http_status")
                ok = isinstance(code, int) and code < 500
                row.update(state=(DeploymentState.LIVE_HTTP_OK if ok else DeploymentState.LIVE_FAILED).value,
                           live={k: obs.get(k) for k in ("status", "classification", "http_status", "content_type",
                                                          "tcp_connected", "error_type", "error") if k in obs},
                           probed_at=time.time())
        rows.append(row)
    totals: dict[str, int] = {}
    for r in rows:
        totals[r["state"]] = totals.get(r["state"], 0) + 1
    out = {"probed": probe, "apps": rows, "totals": totals, "at": time.time()}
    try:
        (store or EventStore()).append_run_event({"event": "DEPLOYMENTS_VERIFIED", "probed": probe, "totals": totals,
                                                  "apps": [{k: r.get(k) for k in ("app", "state", "build_id", "url", "probed_at")}
                                                           for r in rows]})
    except Exception as e:
        out["audit_error"] = f"{type(e).__name__}: {e}"
    return out
