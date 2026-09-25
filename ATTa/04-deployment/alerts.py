#!/usr/bin/env python3
"""
alerts.py — tier 4 of self-healing: tell a human. The last resort only.

An alert carries the full chain of what was already tried (known fix, adapter, LLM,
with each action and its result), so nobody starts blind. It is:
  - written to <ROOT>/state/alerts/<time>-<build>.json   (shown to admins at /alerts)
  - POSTed as JSON to ALERT_WEBHOOK_URL when that is set. The body has a "text" field,
    so a Slack/Discord-style incoming webhook shows it as a message.
"""
from __future__ import annotations
import json, os, time
from urllib.request import Request, urlopen

import builds

# ===================== CONFIG — edit here, nothing below needs reading =====================
ALERT_DIR = builds.ROOT / "state" / "alerts"
# Incoming-webhook URL to ping (Slack, Discord, or anything taking a JSON POST). Blank = page only.
WEBHOOK = os.environ.get("ALERT_WEBHOOK_URL", "")
# Seconds to wait for the webhook.
TIMEOUT = 15
# ==========================================================================================


def raise_alert(build_id: str, layer: str, items: list[dict], chain: list[dict]) -> dict:
    rec = builds.get(build_id) or {}
    now = time.time()
    lines = [f"[APP Builder] Build {build_id} (owner {rec.get('owner', '?')}) is stuck at the {layer} stage "
             f"and self-healing could not fix it."]
    for it in items:
        lines.append(f"- {it.get('app') or 'build'}: {it['key']}" + (f" — {it['why']}" if it.get("why") else ""))
    lines.append(f"Tried: {len(chain)} step(s) — see the alert file for each action and its result.")
    alert = {"schema": "APP_BUILDER_ALERT.v1", "at": now, "build_id": build_id, "owner": rec.get("owner"),
             "layer": layer, "state": rec.get("state"), "error": rec.get("error"), "items": items,
             "chain": chain, "text": "\n".join(lines), "delivered": []}
    if WEBHOOK:
        try:
            req = Request(WEBHOOK, data=json.dumps({"text": alert["text"], "alert": alert}).encode(),
                          headers={"Content-Type": "application/json"}, method="POST")
            with urlopen(req, timeout=TIMEOUT) as r:
                alert["delivered"].append(f"webhook HTTP {r.status}")
        except Exception as e:  # the alert file still gets written
            alert["delivered"].append(f"webhook FAILED: {e}")
    ALERT_DIR.mkdir(parents=True, exist_ok=True)
    (ALERT_DIR / f"{time.strftime('%Y%m%d-%H%M%S', time.gmtime(now))}-{build_id}-{layer}.json").write_text(
        json.dumps(alert, indent=2) + "\n")
    return alert


def system_alert(kind: str, text: str, **fields) -> dict:
    """v115: an alert about the system itself (no build), e.g. an account disabled for a reserved name.
    Carries names only: never passwords, tokens or other secret values."""
    now = time.time()
    alert = {"schema": "APP_BUILDER_ALERT.v1", "at": now, "build_id": "", "layer": kind, "items": [],
             "chain": [], "text": "[APP Builder] " + text, "delivered": [], **fields}
    if WEBHOOK:
        try:
            req = Request(WEBHOOK, data=json.dumps({"text": alert["text"], "alert": alert}).encode(),
                          headers={"Content-Type": "application/json"}, method="POST")
            with urlopen(req, timeout=TIMEOUT) as r:
                alert["delivered"].append(f"webhook HTTP {r.status}")
        except Exception as e:
            alert["delivered"].append(f"webhook FAILED: {e}")
    ALERT_DIR.mkdir(parents=True, exist_ok=True)
    (ALERT_DIR / f"{time.strftime('%Y%m%d-%H%M%S', time.gmtime(now))}-system-{kind}.json").write_text(
        json.dumps(alert, indent=2) + "\n")
    return alert


def recent(limit: int = 50) -> list[dict]:
    out = []
    for p in sorted(ALERT_DIR.glob("*.json"), reverse=True)[:limit]:
        try:
            out.append(json.loads(p.read_text()))
        except (OSError, json.JSONDecodeError):
            continue
    return out
