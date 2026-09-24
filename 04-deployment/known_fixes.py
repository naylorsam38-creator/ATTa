#!/usr/bin/env python3
"""
known_fixes.py — tier 1 of self-healing: deterministic, no AI.

A failure item is matched against a list of known, previously solved problems for its
layer (build, qualify, handoff). A match gives the exact repair actions to run.

Two kinds of entries:
  BUILT_IN  written by hand below, from failures this system is known to produce.
  LEARNED   written by the self-healer itself: when the LLM (tier 3) fixes something
            and the fix is verified, its actions are saved here so next time the same
            failure is fixed by this script without calling the LLM.

A rule can also say `human=` instead of giving actions. That's a KNOWN failure that no
automatic fix can resolve (a missing secret, a bad upload). It skips the adapter and LLM
tiers and goes straight to a human, rather than paying an LLM to rediscover that.
"""
from __future__ import annotations
import json, os, re, tempfile, time

import builds

# ===================== CONFIG — edit here, nothing below needs reading =====================
# Where fixes learned from verified LLM repairs are stored.
LEARNED_FILE = builds.ROOT / "state" / "maintenance" / "learned_fixes.json"
# ==========================================================================================

# Each rule: layer, pattern (regex matched against "<key>\n<detail>"), and either
#   actions: [(action_name, {arg: template})]   templates may use {app} {build_id} and named regex groups
#   retry:   True   the fix is simply to run the layer again (transient failure)
#   human:   "why a person is needed"
BUILT_IN = [
    # ---- build layer
    {"id": "build.non_git_conflict", "layer": "build",
     "pattern": r"CONFLICT_NON_GIT: .*/library/(?P<dir>[^/\s]+)",
     "actions": [("quarantine_library_dir", {"name": "{dir}"})]},
    {"id": "build.git_timeout", "layer": "build",
     "pattern": r"\['git'.*timed out after", "retry": True},
    {"id": "build.git_network", "layer": "build",
     "pattern": r"\['git'.*(Could not resolve host|Failed to connect|Connection (timed out|reset)|early EOF|RPC failed|unable to access)",
     "retry": True},
    {"id": "build.installer_timeout", "layer": "build",
     "pattern": r"install_all\.py.*timed out after", "retry": True},
    {"id": "build.bad_bundle", "layer": "build",
     "pattern": r"does not contain UI_Skin_Capability|readiness marker missing|Skin library index missing|installer missing"
                r"|unsafe archive path|duplicate archive member|archive extracted size exceeds|not deployable"
                r"|Required skin categories missing|preflight is not PASS|Missing [^ ]+/skin-00\d\.(css|json)"
                r"|File is not a zip|multiple copies of",
     "human": "The uploaded bundle itself is invalid. The person who uploaded it needs to fix it and upload again."},
    {"id": "build.repo_unavailable", "layer": "build",
     "pattern": r"Upstream repos unavailable: (?P<repos>.+)",
     "human": "An upstream GitHub repo is gone, private or renamed, and it was the only app for a required category. Fix its entry in upstream_apps.json, then upload again."},
    {"id": "build.package_mapping", "layer": "build",
     "pattern": r"Application skin mapping failed|no deployable app mapping",
     "human": "The skin package's app-to-category mapping is wrong. That lives in the delivered package and needs a new package."},

    # ---- QUALIFIED gate (keys are "<stage>:<code>" from the six-stage watcher)
    {"id": "qualify.launcher_not_executable", "layer": "qualify",
     "pattern": r"^1 INSTALLED:NOT_EXECUTABLE", "actions": [("chmod_launcher", {"app": "{app}"})]},
    {"id": "qualify.overlay_bad_json", "layer": "qualify",
     "pattern": r"^1 INSTALLED:BAD_JSON", "actions": [("repair_overlay_json", {"app": "{app}", "path": "skin.json"})]},
    # Found by syntax_triage.py: a JSON overlay file that won't parse. Mechanical repair only.
    {"id": "qualify.syntax_json", "layer": "qualify",
     "pattern": r"SYNTAX_ERROR (?P<path>[^:\s]+\.json):",
     "actions": [("repair_overlay_json", {"app": "{app}", "path": "{path}"})]},
    {"id": "qualify.playwright_missing", "layer": "qualify",
     "pattern": r"^6 CLEAN:PLAYWRIGHT_UNAVAILABLE", "actions": [("install_browser", {})]},
    {"id": "qualify.browser_check_off", "layer": "qualify",
     "pattern": r"^6 CLEAN:NOT_RUN",
     "human": "APP_BUILDER_BROWSER_CHECK is off, so nothing can qualify. Only the owner may turn it back on."},
    {"id": "qualify.toolchain_too_old", "layer": "qualify",
     "pattern": r"^6 CLEAN:(TOOLCHAIN_TOO_OLD|NO_INTAKE_RECORD)",
     "human": "This app needs a newer toolchain (Go/Node/PHP/Python) than the server has, or has no intake record. Install the version shown, or give the app a Dockerfile so it builds in a container."},
    # ---- app runner (2 APP_UP codes it produces)
    {"id": "qualify.no_docker", "layer": "qualify",
     "pattern": r"^2 APP_UP:RUNNER_NO_DOCKER",
     "human": "Docker is not installed or won't start on this server, so no app can be started. Re-run `bash run` (bootstrap installs it) or install Docker + the compose plugin."},
    {"id": "qualify.host_no_ipv6", "layer": "qualify",
     "pattern": r"^2 APP_UP:RUNNER_HOST_NO_IPV6",
     "human": "This server's kernel has IPv6 switched off and the app listens on IPv6. Turn IPv6 on for the server (default on AWS), then re-run the build."},
    {"id": "qualify.proxy_port_taken", "layer": "qualify",
     "pattern": r"^2 APP_UP:PROXY_NOT_STARTED\n.*skin proxy port (?P<port>\d+) is already taken",
     "human": "Another program holds this app's skin-proxy port. Stop it (see `ss -ltnp | grep <port>`), then re-run the build."},
    # Seen at run time instead of by name: the app sends the visitor to a different address (its own
    # subdomain or configured host), off the skin proxy. Needs its base URL / single-domain setting.
    {"id": "qualify.redirect_off_proxy", "layer": "qualify",
     "pattern": r"^(3 PROXY_UP|6 CLEAN):REDIRECT_OFF_PROXY",
     "human": "The app sends visitors to another address (its own subdomain or a configured host), off the skin proxy. Set the app's base URL / single-domain setting to the proxy address."},
    {"id": "qualify.no_targets", "layer": "qualify",
     "pattern": r"^TARGETS:NO_TARGETS",
     "human": "No app targets are registered. Each app must be started with its run-ui.sh (with TARGET_URL) before a build can qualify."},

    # ---- Coolify hand-off
    {"id": "handoff.not_configured", "layer": "handoff",
     "pattern": r"^NOT_CONFIGURED", "human": "COOLIFY_URL and COOLIFY_TOKEN are not set in the server's .env."},
    {"id": "handoff.auth_rejected", "layer": "handoff",
     "pattern": r"^ERROR:HTTP (401|403)", "human": "Coolify refused the request: either the API token is invalid / lacks the deploy and read permissions, or the API is switched off in Coolify (Settings -> Advanced -> API Access)."},
    {"id": "handoff.unmapped", "layer": "handoff",
     "pattern": r"^UNMAPPED", "actions": [("lookup_coolify_uuid", {"app": "{app}"})]},
    {"id": "handoff.stale_uuid", "layer": "handoff",
     "pattern": r"^ERROR:HTTP 404", "actions": [("lookup_coolify_uuid", {"app": "{app}"})]},
    {"id": "handoff.transient", "layer": "handoff",
     "pattern": r"^ERROR:(HTTP 5\d\d|HTTP 429|HTTP 408|UNREACHABLE)", "retry": True},
]


def learned() -> list[dict]:
    try:
        d = json.loads(LEARNED_FILE.read_text())
        return d if isinstance(d, list) else []
    except (FileNotFoundError, json.JSONDecodeError):
        return []


def learn(layer: str, key: str, app: str | None, build_id: str, actions: list[dict], note: str) -> None:
    """Save a verified LLM fix. The same failure key on any app replays the same actions."""
    def generalise(v):
        if isinstance(v, str):
            if app and v == app: return "{app}"
            if v == build_id: return "{build_id}"
        return v
    steps = [{"name": a["name"], "args": {k: generalise(v) for k, v in a["args"].items()}} for a in actions]
    rows = [r for r in learned() if not (r["layer"] == layer and r["key"] == key)]
    rows.append({"id": f"learned.{layer}.{len(rows) + 1}", "layer": layer, "key": key, "actions": steps,
                 "learned_at": time.time(), "from_build": build_id, "note": note[:500]})
    LEARNED_FILE.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=LEARNED_FILE.parent, prefix=".learned.")
    with os.fdopen(fd, "w") as f:
        json.dump(rows, f, indent=2); f.write("\n")
    os.replace(tmp, LEARNED_FILE)


def _fill(template, ctx):
    if isinstance(template, str):
        try:
            return template.format(**ctx)
        except (KeyError, IndexError):
            return template
    return template


def match(layer: str, item: dict, build_id: str) -> dict | None:
    """The first known fix for this failure, resolved to concrete actions, or None."""
    text = item["key"] + "\n" + (item.get("detail") or "")
    base = {"app": item.get("app") or "", "build_id": build_id}
    for r in learned():  # learned fixes are exact-key matches and are checked first
        if r["layer"] == layer and r["key"] == item["key"]:
            return {"rule": r["id"], "actions": [(s["name"], {k: _fill(v, base) for k, v in s["args"].items()})
                                                 for s in r["actions"]]}
    for r in BUILT_IN:
        if r["layer"] != layer:
            continue
        m = re.search(r["pattern"], text)
        if not m:
            continue
        ctx = {**base, **{k: v for k, v in m.groupdict().items() if v is not None}}
        if r.get("human"):
            return {"rule": r["id"], "human": r["human"]}
        if r.get("retry"):
            return {"rule": r["id"], "actions": [], "retry": True}
        return {"rule": r["id"], "actions": [(n, {k: _fill(v, ctx) for k, v in a.items()}) for n, a in r["actions"]]}
    return None
