#!/usr/bin/env python3
"""
capability_adapter.py — tier 2 of self-healing: the Capability adapter.

The Capability Port (port.js) runs in the browser and has no repair interface, and the
dormant-hook rule forbids anything on the server from calling or mounting it. What the
adapter *does* own is each app's server-side capability overlay, the pieces the installer
writes and labels "source: Capability adapter" in deployment.json:

    <app>/.ui-capability/{skin.css, skin.json, deployment.json, run-ui.sh,
                          ui-bridge/proxy.js, capability-port/port.js}
    state/apps/<app>.json   (the target registration: ui_dir, target_url, proxy_url)
    the running UI proxy    (listening on the registered proxy port)

Tier 1 matches error text. The adapter instead inspects those pieces directly against the
delivered package and says whether the broken piece is one of its own. If it is, it
repairs it with its own logic: reinstall from the package, restore the launcher mode,
re-point a stale registration, or restart a proxy that isn't running. Anything outside
those pieces (the app itself down, a mounted capability, Coolify) is "not mine", and
escalation moves on.
"""
from __future__ import annotations
import hashlib, os
from pathlib import Path
from urllib.parse import urlparse

import repair_actions as ra

# Files the overlay must hold, and which ones must match the package byte for byte.
REQUIRED = ["skin.css", "skin.json", "deployment.json", "run-ui.sh", "ui-bridge/proxy.js", "capability-port/port.js"]
FROM_PACKAGE = {"capability-port/port.js": "capability-port/port.js", "ui-bridge/proxy.js": "ui-bridge/proxy.js"}
# Watcher stage codes the adapter treats as its own pieces (the rest are "not mine").
OWN_STAGES = ("TARGETS:", "1 INSTALLED:", "3 PROXY_UP:REFUSED", "3 PROXY_UP:TIMEOUT", "4 SKIN:", "5 HOOK:PORT_")


def _sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def inspect(app: str) -> list[str]:
    """What's wrong with this app's capability pieces, as plain findings (empty list = healthy)."""
    findings = []
    try:
        _, t = ra.target(app)
    except ra.ActionError:
        return ["no target registration"]
    ui = Path(t.get("ui_dir") or "")
    if not ui.is_dir():
        return ["registered ui_dir missing"]
    for f in REQUIRED:
        p = ui / f
        if not p.is_file() or p.stat().st_size == 0:
            findings.append(f"missing or empty: {f}")
    for f, src in FROM_PACKAGE.items():
        p, s = ui / f, ra.PKG / "out" / src
        if p.is_file() and s.is_file() and _sha(p) != _sha(s):
            findings.append(f"differs from package: {f}")
    launcher = ui / "run-ui.sh"
    if launcher.is_file() and not os.access(launcher, os.X_OK):
        findings.append("launcher not executable")
    port = urlparse(t.get("proxy_url", "")).port
    if port and not ra._port_open(port):
        findings.append("proxy not listening")
    return findings


def plan(item: dict) -> dict | None:
    """Tier 2 entry point. None = not one of the adapter's pieces, or nothing found wrong with them."""
    if not item.get("key", "").startswith(OWN_STAGES):
        return None
    app = item.get("app")
    if not app:
        return None
    findings = inspect(app)
    if not findings:
        return None
    steps = []
    if "no target registration" in findings:
        return {"findings": findings, "actions": []}  # nothing registered: needs run-ui.sh with a real TARGET_URL
    if "registered ui_dir missing" in findings:
        steps.append(("reregister_target", {"app": app}))
    if any(f.startswith(("missing or empty", "differs from package")) for f in findings):
        steps.append(("reinstall_app_overlay", {"app": app}))
    elif "launcher not executable" in findings:
        steps.append(("chmod_launcher", {"app": app}))
    if "proxy not listening" in findings:
        steps.append(("start_proxy", {"app": app}))
    return {"findings": findings, "actions": steps}
