"""
Record-driven, read-only failure diagnostic.

This module is deliberately separate from app_runner.diagnose(), which is the
live operational failure-rule matcher. It consumes completed failure records
and their evidence; it never repairs, reruns, deploys, or rewrites evidence.

Contract:
  observation/classification -> evidence -> policy action -> independent root cause

A policy rule may permit an action only when its evidence predicate is met.
Otherwise the finding is explicitly incomplete/ambiguous and cannot authorize
a stronger action.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

POLICY_VERSION = "record-diagnostic-v1"
ALLOWED_ACTIONS = frozenset(
    {"AUTO", "RETRY_ONLY", "APPROVAL_REQUIRED", "ESCALATE", "NO_ACTION"}
)
MAX_READ_BYTES = 2 * 1024 * 1024
MAX_MATCHES = 20
MAX_TEXT = 1200

# The policy is intentionally conservative. A classifier cannot authorize an
# action merely because a log message "sounds like" a known problem.
POLICY = {
    "BUILD_CONTEXT_SOURCE_MISSING": {
        "required": ("missing_source",),
        "allowed_action": "ESCALATE",
    },
    "STARTUP_CONFIG_MISSING": {
        "required": ("missing_required_config",),
        "allowed_action": "APPROVAL_REQUIRED",
    },
    "STARTUP_DEPENDENCY_UNAVAILABLE": {
        "required": ("dependency_unavailable",),
        "allowed_action": "RETRY_ONLY",
    },
    "CONTAINER_EXITED": {
        "required": ("container_exited",),
        "allowed_action": "ESCALATE",
    },
    "CONTAINER_OOM": {
        "required": ("oom_observed",),
        "allowed_action": "APPROVAL_REQUIRED",
    },
    "STARTUP_TIMEOUT": {
        "required": ("startup_timeout",),
        "allowed_action": "RETRY_ONLY",
    },
    "SELECTED_SERVICE_NOT_DECLARED": {
        "required": ("selected_service_not_declared",),
        "allowed_action": "ESCALATE",
    },
    "MULTI_SERVICE_TARGET_AMBIGUOUS": {
        "required": ("multi_service_ambiguous",),
        "allowed_action": "ESCALATE",
    },
    "TCP_UNREACHABLE": {
        "required": ("tcp_unreachable",),
        "allowed_action": "RETRY_ONLY",
    },
    "HTTP_PROTOCOL_MISMATCH": {
        "required": ("http_protocol_mismatch",),
        "allowed_action": "ESCALATE",
    },
    "TLS_PROTOCOL_MISMATCH": {
        "required": ("tls_protocol_mismatch",),
        "allowed_action": "ESCALATE",
    },
    "APPLICATION_RESPONSE_INVALID": {
        "required": ("application_response_invalid",),
        "allowed_action": "ESCALATE",
    },
    "PROFILE_JOURNEY_INCOMPATIBLE": {
        "required": ("profile_journey_incompatible",),
        "allowed_action": "ESCALATE",
    },
    "PROFILE_UNSUPPORTED": {
        "required": ("profile_unsupported",),
        "allowed_action": "ESCALATE",
    },
    "UPSTREAM_502": {
        "required": ("http_502",),
        "allowed_action": "ESCALATE",
    },
    "CORS_BLOCKED": {
        "required": ("cors_blocked",),
        "allowed_action": "ESCALATE",
    },
    "INJECTION_ASSET_MISSING": {
        "required": ("injection_asset_missing",),
        "allowed_action": "ESCALATE",
    },
    "BROWSER_NETWORK_ERROR": {
        "required": ("browser_network_error",),
        "allowed_action": "RETRY_ONLY",
    },
    "AUTH_STRATEGY_MISSING": {
        "required": ("auth_strategy_missing",),
        "allowed_action": "ESCALATE",
    },
    "LOGIN_FLOW_FAILED": {
        "required": ("login_flow_failed",),
        "allowed_action": "ESCALATE",
    },
    "ACCOUNT_SETUP_REQUIRED": {
        "required": ("account_setup_required",),
        "allowed_action": "APPROVAL_REQUIRED",
    },
    "CONCURRENT_RUN_OVERLAP_OBSERVED": {
        "required": ("overlap_observed", "overlap_supporting_evidence"),
        "allowed_action": "ESCALATE",
    },
    "EVIDENCE_IDENTITY_MISMATCH": {
        "required": ("identity_mismatch",),
        "allowed_action": "NO_ACTION",
    },
    "STALE_EVIDENCE": {
        "required": ("stale_evidence",),
        "allowed_action": "NO_ACTION",
    },
    "EVIDENCE_IDENTITY_INCOMPLETE": {
        "required": ("identity_incomplete",),
        "allowed_action": "NO_ACTION",
    },
    "APP_IDENTITY_MISSING": {
        "required": ("app_identity_missing",),
        "allowed_action": "NO_ACTION",
    },
    "APP_IDENTITY_EXTRA": {
        "required": ("app_identity_extra",),
        "allowed_action": "NO_ACTION",
    },
    "APP_IDENTITY_DUPLICATE": {
        "required": ("app_identity_duplicate",),
        "allowed_action": "NO_ACTION",
    },
    "INCOMPLETE_OR_AMBIGUOUS": {
        "required": (),
        "allowed_action": "ESCALATE",
    },
}

# Only these metadata keys may be copied into an audit result.
IDENTITY_KEYS = (
    "app", "app_id", "run_id", "revision", "deployment_revision",
    "attempt", "attempt_id", "started_at", "finished_at", "timestamp",
    "status", "stage", "code",
)

SECRET_KEY_RE = re.compile(
    r"(password|passwd|secret|token|api[_-]?key|private[_-]?key|authorization|cookie)",
    re.I,
)


@dataclass(frozen=True)
class Observation:
    kind: str
    source: str
    field: str
    observation: str
    timestamp: str | None = None


def _bounded_text(path: Path) -> str:
    try:
        with path.open("rb") as f:
            data = f.read(MAX_READ_BYTES)
    except OSError:
        return ""
    return data.decode("utf-8", errors="replace")


def _safe_value(key: str, value: Any) -> Any:
    if SECRET_KEY_RE.search(str(key)):
        return "[REDACTED]"
    if isinstance(value, str):
        value = re.sub(
            r"(?i)(password|passwd|secret|token|api[_-]?key|authorization|cookie)\s*[:=]\s*\S+",
            r"\1=[REDACTED]",
            value,
        )
        return value[:MAX_TEXT]
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    return str(value)[:MAX_TEXT]


def _walk(obj: Any, prefix: str = "") -> Iterable[tuple[str, Any]]:
    if isinstance(obj, dict):
        for k, v in obj.items():
            p = f"{prefix}.{k}" if prefix else str(k)
            yield p, v
            yield from _walk(v, p)
    elif isinstance(obj, list):
        for i, v in enumerate(obj[:100]):
            yield f"{prefix}[{i}]", v
            yield from _walk(v, f"{prefix}[{i}]")


def _json_file(path: Path) -> Any:
    try:
        return json.loads(_bounded_text(path))
    except (ValueError, OSError):
        return None


def _identity(obj: Any) -> dict[str, Any]:
    out = {}
    if isinstance(obj, dict):
        for path, value in _walk(obj):
            key = path.rsplit(".", 1)[-1].lower()
            if key in {x.lower() for x in IDENTITY_KEYS} and value not in (None, ""):
                # Prefer scalar values and do not expose secrets.
                if isinstance(value, (str, int, float, bool)):
                    out[key] = _safe_value(key, value)
    return out


def _fingerprint(path: Path) -> str:
    h = hashlib.sha256()
    try:
        with path.open("rb") as f:
            while True:
                chunk = f.read(1024 * 1024)
                if not chunk:
                    break
                h.update(chunk)
    except OSError:
        return "UNREADABLE"
    return h.hexdigest()


def _evidence_ref(path: Path, field: str, observation: str,
                  timestamp: str | None = None) -> dict[str, Any]:
    return {
        "artifact_path": str(path),
        "artifact_sha256": _fingerprint(path),
        "field": field,
        "observation": observation[:MAX_TEXT],
        "timestamp": timestamp,
    }


def _contains(text: str, patterns: Iterable[str]) -> bool:
    return any(re.search(p, text, re.I | re.M) for p in patterns)


def extract_observations(record_path: Path) -> list[Observation]:
    """Extract observations only. No cause or action is inferred here."""
    record = _json_file(record_path)
    if record is None:
        return []

    observations: list[Observation] = []
    blob = _bounded_text(record_path)

    if _contains(blob, [r'["\']?[^"\']+["\']?\s*:\s*["\']?[^"\']*not found']):
        observations.append(Observation(
            "missing_source", str(record_path), "record",
            "record contains a missing-file/source observation"))
    if _contains(blob, [r"failed to (?:calculate checksum|solve).*not found",
                        r"COPY .*not found", r'"/[^"]+": not found']):
        observations.append(Observation(
            "missing_source", str(record_path), "record",
            "build evidence explicitly reports a missing COPY/build source"))

    if _contains(blob, [r"jwt_secret.*not been set", r"BACKEND_URL", r"input.*undefined",
                        r"required.*(?:env|environment|config)", r"Invalid URL"]):
        observations.append(Observation(
            "missing_required_config", str(record_path), "record",
            "startup evidence contains an explicit missing/invalid configuration condition"))

    if _contains(blob, [r"redis", r"connection refused", r"dependency.*(?:unavailable|not ready)",
                        r"postgres", r"mysql", r"pgbouncer"]):
        if _contains(blob, [r"connection refused", r"dependency.*(?:unavailable|not ready)",
                             r"redis.*(?:crash|refused)", r"postgres.*(?:refused|unavailable)",
                             r"mysql.*(?:refused|unavailable)"]):
            observations.append(Observation(
                "dependency_unavailable", str(record_path), "record",
                "startup evidence explicitly indicates a dependency was unavailable"))

    if _contains(blob, [r"__EXITED__", r"container\.exited", r"container.*stopped"]):
        observations.append(Observation(
            "container_exited", str(record_path), "record",
            "record explicitly reports container exit/stoppage"))

    if _contains(blob, [r"out of memory", r"\boom killed\b", r"oomkilled", r"exit.*137"]):
        observations.append(Observation(
            "oom_observed", str(record_path), "record",
            "record contains explicit OOM evidence"))

    if _contains(blob, [r"TIMEOUT", r"timed out", r"app\.still_starting", r"START_TIMEOUT"]):
        observations.append(Observation(
            "startup_timeout", str(record_path), "record",
            "record explicitly reports a startup timeout"))

    if _contains(blob, [r"502\s*\(Bad Gateway\)", r"\b502\b", r"BROWSER_HTTP.*502"]):
        observations.append(Observation(
            "http_502", str(record_path), "record",
            "browser/proxy evidence contains HTTP 502"))

    if _contains(blob, [r"CORS policy", r"blocked by CORS"]):
        observations.append(Observation(
            "cors_blocked", str(record_path), "record",
            "browser evidence explicitly reports a CORS block"))

    if _contains(blob, [r"NO_LINK", r"skin.*(?:missing|not present)", r"injection.*asset"]):
        observations.append(Observation(
            "injection_asset_missing", str(record_path), "record",
            "record explicitly reports the expected injection/skin asset was absent"))

    if _contains(blob, [r"ERR_NETWORK_CHANGED", r"Failed to load resource.*network"]):
        observations.append(Observation(
            "browser_network_error", str(record_path), "record",
            "browser evidence explicitly reports a network-change/resource error"))

    if _contains(blob, [r"ACCOUNT_NO_STRATEGY", r"ACCOUNT_NO", r"no account strategy",
                        r"no login form.*no internal link"]):
        observations.append(Observation(
            "auth_strategy_missing", str(record_path), "record",
            "browser/journey evidence explicitly says no usable account strategy exists"))

    if _contains(blob, [r"SMOKE_NO_CONTROL", r"JOURNEY_NOT_AUTHORED"]):
        observations.append(Observation(
            "profile_journey_incompatible", str(record_path), "record",
            "record explicitly says the smoke/journey is not authored or has no control"))

    if _contains(blob, [r"selected.*(?:helper|realtime|demo)", r"wrong.*service",
                        r"webapp-demo", r"coolify-helper"]):
        observations.append(Observation(
            "selected_service_not_declared", str(record_path), "record",
            "record contains evidence that the selected service differed from the intended web target"))

    if _contains(blob, [r"HTTP.*HTTPS", r"plain HTTP.*HTTPS", r"protocol mismatch",
                        r"wrong protocol", r"parser.*response"]):
        observations.append(Observation(
            "http_protocol_mismatch", str(record_path), "record",
            "record contains explicit protocol-mismatch evidence"))

    return observations


def _identity_check(record_path: Path, related: list[Path]) -> tuple[dict[str, Any], list[Observation]]:
    record = _json_file(record_path)
    base = _identity(record)
    observations: list[Observation] = []
    all_ids = [base]
    for p in related:
        obj = _json_file(p)
        if obj is not None:
            all_ids.append(_identity(obj))

    required = ("app", "run_id", "revision", "attempt")
    if any(k not in base for k in required):
        observations.append(Observation(
            "identity_incomplete", str(record_path), "identity",
            "required deployment identity fields are missing"))
    for other, p in zip(all_ids[1:], related):
        for k in required:
            if k in base and k in other and str(base[k]) != str(other[k]):
                observations.append(Observation(
                    "identity_mismatch", str(p), k,
                    f"{k} does not match the primary failure record"))
    return base, observations


def classify(record_path: Path, related: list[Path] | None = None) -> dict[str, Any]:
    related = related or []
    identity, identity_obs = _identity_check(record_path, related)
    obs = identity_obs + extract_observations(record_path)

    # Identity failures are evaluated before application findings.
    if any(o.kind == "identity_mismatch" for o in obs):
        classification = "EVIDENCE_IDENTITY_MISMATCH"
    elif any(o.kind == "identity_incomplete" for o in obs):
        classification = "EVIDENCE_IDENTITY_INCOMPLETE"
    else:
        precedence = [
            "missing_source", "missing_required_config", "dependency_unavailable",
            "container_exited", "oom_observed", "startup_timeout",
            "selected_service_not_declared", "http_protocol_mismatch", "http_502",
            "cors_blocked", "injection_asset_missing", "browser_network_error",
            "auth_strategy_missing", "profile_journey_incompatible",
        ]
        classification = next(
            (c for c in precedence if any(o.kind == c for o in obs)),
            "INCOMPLETE_OR_AMBIGUOUS",
        )

    kind_to_class = {
        "missing_source": "BUILD_CONTEXT_SOURCE_MISSING",
        "missing_required_config": "STARTUP_CONFIG_MISSING",
        "dependency_unavailable": "STARTUP_DEPENDENCY_UNAVAILABLE",
        "container_exited": "CONTAINER_EXITED",
        "oom_observed": "CONTAINER_OOM",
        "startup_timeout": "STARTUP_TIMEOUT",
        "selected_service_not_declared": "SELECTED_SERVICE_NOT_DECLARED",
        "http_protocol_mismatch": "HTTP_PROTOCOL_MISMATCH",
        "http_502": "UPSTREAM_502",
        "cors_blocked": "CORS_BLOCKED",
        "injection_asset_missing": "INJECTION_ASSET_MISSING",
        "browser_network_error": "BROWSER_NETWORK_ERROR",
        "auth_strategy_missing": "AUTH_STRATEGY_MISSING",
        "profile_journey_incompatible": "PROFILE_JOURNEY_INCOMPATIBLE",
    }
    classification = kind_to_class.get(classification, classification)
    rule = POLICY[classification]
    action = rule["allowed_action"]
    refs = [
        _evidence_ref(Path(o.source), o.field, o.observation, o.timestamp)
        for o in obs
        if Path(o.source).exists()
    ]
    # The cause is deliberately independent. This module never promotes an
    # observation to a causal claim.
    root_cause = {
        "value": "UNKNOWN",
        "basis": "The diagnostic establishes an observable condition only; no causal proof was supplied.",
    }

    return {
        "schema_version": POLICY_VERSION,
        "app": identity.get("app") or identity.get("app_id"),
        "classification": classification,
        "evidence": refs,
        "root_cause": root_cause,
        "allowed_action": action,
        "suggested_action": _suggested_action(classification),
        "remediation_id": None,
        "deployment_identity": {
            k: identity.get(k) for k in IDENTITY_KEYS if k in identity
        },
        "policy_version": POLICY_VERSION,
    }


def _suggested_action(classification: str) -> str:
    return {
        "BUILD_CONTEXT_SOURCE_MISSING": "Verify the declared build context/source tree before changing ATTa.",
        "STARTUP_CONFIG_MISSING": "Check the declared configuration contract; do not invent credentials or secrets.",
        "STARTUP_DEPENDENCY_UNAVAILABLE": "Verify dependency readiness and declared topology; retry only if policy permits.",
        "CONTAINER_EXITED": "Inspect the exit evidence and declared startup contract.",
        "CONTAINER_OOM": "Review resource limits and memory evidence before changing them.",
        "STARTUP_TIMEOUT": "Verify startup readiness and dependency timing before a bounded retry.",
        "SELECTED_SERVICE_NOT_DECLARED": "Check the declared service target and probe mapping.",
        "HTTP_PROTOCOL_MISMATCH": "Check the declared service protocol and selected probe target.",
        "UPSTREAM_502": "Correlate proxy, upstream, and browser evidence; do not infer the upstream cause from 502 alone.",
        "CORS_BLOCKED": "Compare the proxy origin and served asset origin against the declared web contract.",
        "INJECTION_ASSET_MISSING": "Verify the expected skin/injection asset and insertion contract.",
        "BROWSER_NETWORK_ERROR": "Check for transient network changes and fleet overlap before retrying.",
        "AUTH_STRATEGY_MISSING": "Define an explicit account/journey strategy; do not guess credentials.",
        "PROFILE_JOURNEY_INCOMPATIBLE": "Verify the qualification profile and authored journey.",
        "EVIDENCE_IDENTITY_MISMATCH": "Rebind or recollect evidence; do not act on mismatched artifacts.",
        "EVIDENCE_IDENTITY_INCOMPLETE": "Recollect missing identity metadata before acting.",
        "INCOMPLETE_OR_AMBIGUOUS": "Collect the missing evidence; do not upgrade the finding.",
    }.get(classification, "Review the evidence against the versioned policy.")


def audit_input_fingerprints(paths: Iterable[Path]) -> dict[str, str]:
    return {str(p): _fingerprint(p) for p in paths}


def fingerprints_unchanged(before: dict[str, str]) -> bool:
    return all(_fingerprint(Path(p)) == digest for p, digest in before.items())


def run(records_root: Path, expected_apps: Iterable[str]) -> dict[str, Any]:
    """Read every expected app's failure record. Never mutates records_root."""
    expected_apps = list(expected_apps)
    results = []
    missing = []
    for app in expected_apps:
        candidates = sorted(Path(records_root).glob(f"{app}/*.json"))
        # Do not silently choose among duplicate records.
        if len(candidates) == 0:
            missing.append(app)
            continue
        if len(candidates) > 1:
            results.append({
                "app": app,
                "classification": "APP_IDENTITY_DUPLICATE",
                "evidence": [],
                "root_cause": {"value": "UNKNOWN", "basis": "Multiple candidate records exist."},
                "allowed_action": "NO_ACTION",
                "suggested_action": "Resolve record identity before diagnosis.",
                "remediation_id": None,
                "deployment_identity": {},
                "policy_version": POLICY_VERSION,
            })
            continue
        results.append(classify(candidates[0]))

    return {
        "schema_version": POLICY_VERSION,
        "read_only": True,
        "expected_count": len(expected_apps),
        "processed_count": len(results),
        "missing_apps": missing,
        "findings": results,
    }
