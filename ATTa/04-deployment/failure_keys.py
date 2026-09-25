#!/usr/bin/env python3
"""
failure_keys.py — ONE key for one failure. v111a.

Before this, a learned fix was looked up by (layer, key) where "key" was whatever the layer
produced: a six-stage code ("1 INSTALLED:NOT_EXECUTABLE"), a Coolify status ("ERROR:HTTP 404"),
or — for the build layer — 160 characters of an error message with the numbers stripped out.
That last one is a WEAK key: two unrelated errors that happen to start the same way share it,
and a fix learned on one replays on the other.

Now every failure gets a FailureKey:

    v2|<layer>|<key>

  version  the format of everything after it. If it ever changes, the version changes.
  layer    build | qualify | handoff
  key      a STRONG key, or the literal UNKNOWN_FAILURE.

The normaliser decides whether a key is strong. Strong keys are the ones the system itself
produces from a code path (stage codes, Coolify statuses, the named build-failure families
below). Anything else — free-text error we cannot place — becomes UNKNOWN_FAILURE, and a
learned fix is never stored for or matched against UNKNOWN_FAILURE. The LLM still sees the
full detail and can still repair it; the system just refuses to REMEMBER a fix under a key
that would match the wrong thing next time. The raw text stays on the item as `raw_key` so
the evidence is never lost.

Versions: rules carry the version they were written under. A rule whose version is not in
KNOWN_VERSIONS is never matched (refused, and shown on rule_health.md as such). There is no
migration code: when v3 exists, v3 handling is written then.
"""
from __future__ import annotations
import re

# ===================== CONFIG — edit here, nothing below needs reading =====================
# The FailureKey format this code writes. Bump only when the layout after "v2|" changes.
VERSION = 2
# Versions this code will read and match. A rule with any other version is refused.
KNOWN_VERSIONS = (2,)
# The key given to any failure the normaliser cannot place. Never learned, never matched.
UNKNOWN = "UNKNOWN_FAILURE"
# Build-layer failure families. Order matters: first match wins. Add a family here and any
# free-text build error matching its pattern gets a strong key instead of UNKNOWN_FAILURE.
BUILD_FAMILIES = [
    ("CONFLICT_NON_GIT",     r"CONFLICT_NON_GIT"),
    ("GIT_TIMEOUT",          r"\['git'.*timed out after"),
    ("GIT_NETWORK",          r"\['git'.*(Could not resolve host|Failed to connect|Connection (timed out|reset)|early EOF|RPC failed|unable to access)"),
    ("INSTALLER_TIMEOUT",    r"install_all\.py.*timed out after"),
    ("REPO_UNAVAILABLE",     r"Upstream repos unavailable"),
    ("PACKAGE_MAPPING",      r"Application skin mapping failed|no deployable app mapping"),
    ("BAD_BUNDLE",           r"does not contain UI_Skin_Capability|readiness marker missing|Skin library index missing|installer missing"
                             r"|unsafe archive path|duplicate archive member|archive extracted size exceeds|not deployable"
                             r"|Required skin categories missing|preflight is not PASS|Missing [^ ]+/skin-00\d\.(css|json)"
                             r"|File is not a zip|multiple copies of"),
]
# Strong-key shapes for the other layers: the system produces these from code, not from prose.
QUALIFY_STRONG = r"^(\d [A-Z_]+:[A-Z0-9_]+(:[A-Z0-9_]*)?|TARGETS:NO_TARGETS)$"
HANDOFF_STRONG = r"^(NOT_CONFIGURED|UNMAPPED|ERROR:(HTTP \d{3}|UNREACHABLE))$"
# ==========================================================================================

LAYERS = ("build", "qualify", "handoff")


def normalise(layer: str, key: str, detail: str = "") -> str:
    """The strong key for this failure, or UNKNOWN."""
    if layer not in LAYERS:
        raise ValueError(layer)
    key = (key or "").strip()
    if layer == "build":
        text = key + "\n" + (detail or "")
        for name, pat in BUILD_FAMILIES:
            if re.search(pat, text):
                return name
        # Already a family name (a caller passing a normalised key back in) stays as it is.
        if key in {n for n, _ in BUILD_FAMILIES}:
            return key
        return UNKNOWN
    if layer == "qualify":
        return key if re.match(QUALIFY_STRONG, key) else UNKNOWN
    return key if re.match(HANDOFF_STRONG, key) else UNKNOWN


def make(layer: str, key: str, detail: str = "") -> str:
    """The FailureKey string for a failure item: v2|layer|strong-key."""
    return f"v{VERSION}|{layer}|{normalise(layer, key, detail)}"


def parse(fk: str) -> dict | None:
    """Split a FailureKey. None if it is not one this code understands (bad shape or unknown version)."""
    if not isinstance(fk, str):
        return None
    parts = fk.split("|", 2)
    if len(parts) != 3 or not parts[0].startswith("v") or not parts[0][1:].isdigit():
        return None
    v = int(parts[0][1:])
    if v not in KNOWN_VERSIONS or parts[1] not in LAYERS or not parts[2]:
        return None
    return {"version": v, "layer": parts[1], "key": parts[2]}


def is_unknown(fk: str) -> bool:
    p = parse(fk)
    return p is None or p["key"] == UNKNOWN


def for_rule(rule: dict) -> str | None:
    """The FailureKey a learned-fix entry is looked up by.
    v111+ entries carry `failure_key`. A v110 entry (layer + key, no failure_key) is read as the same
    fact: its key was written by the same layer code, so it is normalised the same way. That is not a
    migration — nothing is rewritten — it is reading an older shape. An entry whose failure_key has a
    version this code does not know is refused: None."""
    fk = rule.get("failure_key")
    if fk is not None:
        return fk if parse(fk) else None
    if rule.get("layer") in LAYERS and rule.get("key"):
        return make(rule["layer"], rule["key"], "")
    return None


def refused(rule: dict) -> str | None:
    """Why this rule can never match, or None if it can."""
    fk = rule.get("failure_key")
    if fk is not None and parse(fk) is None:
        return f"failure_key version not understood ({str(fk)[:40]}); this code reads v{','.join(map(str, KNOWN_VERSIONS))} only"
    got = for_rule(rule)
    if got is None:
        return "no failure_key and no layer/key to derive one from"
    if is_unknown(got):
        return "keyed on UNKNOWN_FAILURE: too weak to replay"
    return None
