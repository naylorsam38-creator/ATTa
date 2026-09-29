"""The repair gate: the one place the self-healer (maintenance.py) asks before running a repair action.

For every action, immediately before it runs:

  1. identity   the failure's evidence names its job completely. A qualification failure: app, run id,
                attempt, correlation id, deployment revision and app revision, read from the result the
                failure came from (the result's `verification` block, written by app_runner). A build or
                hand-off failure: its build record.
  2. freshness  the evidence was recorded no longer ago than APP_BUILDER_CONTROL_MAX_EVIDENCE_AGE seconds
                (default 6 h) and not in the future.
  3. unchanged  the evidence re-read now from the build record is the evidence the self-healer acted on, and
                (qualification) the app and the ATTa deployment are still the revisions the failure was seen
                on. A change made by an earlier repair the gate itself recorded in this healing round is
                accounted for; any other change means the evidence is stale.
  4. policy     the versioned policy (policy.py) says AUTO, or APPROVAL_REQUIRED and a person has approved
                this exact remediation id (single use, with an expiry). Anything else is refused.

Every decision and every repair (started, returned, raised) goes to the hash-chained audit log.

Modes (APP_BUILDER_CONTROL_GATE):
  off       the gate is not consulted; repairs run exactly as in v121.3; nothing is recorded.
  observe   (default) every check runs and is recorded; nothing is blocked. Use it to see what enforce would
            do, and to scaffold a policy from real repairs (`python3 -m atta_control policy scaffold`).
  enforce   a repair runs only if every check passes. A refusal reaches maintenance.py as the action's
            error, so that tier counts as FIX_FAILED and healing moves to the next tier or to a person,
            exactly as for any other failed fix. An unrecognised mode value is treated as enforce.

The gate never repairs anything itself and never runs an action that is not in repair_actions.ACTIONS."""
from __future__ import annotations

import os
import threading
import time
from typing import Any, Callable, Mapping, Optional

import failure_keys

from .models import AppIdentity, RepairAuthorization
from .records import safe_id
from .policy import PolicyError, RepairPolicy, load_policy
from .store import EventStore, fingerprint, sha256_bytes
from redact import redact

# ===================== CONFIG — edit here, nothing below needs reading =====================
MODES = ("off", "observe", "enforce")
DEFAULT_MODE = "observe"
# Oldest evidence (seconds) a repair may act on. A healing round starts right after the qualification it
# heals, so this is generous; evidence older than this belongs to a run nobody is healing any more.
DEFAULT_MAX_EVIDENCE_AGE = 6 * 3600
# Clock skew tolerated for a timestamp in the future.
FUTURE_SKEW = 60
# How long an approval stays usable when the person gives no expiry.
DEFAULT_APPROVAL_TTL = 24 * 3600
# ==========================================================================================


class RepairDenied(RuntimeError):
    """Raised in enforce mode instead of running a repair. maintenance.py records it as the action's error."""


def mode() -> str:
    raw = os.environ.get("APP_BUILDER_CONTROL_GATE", DEFAULT_MODE).strip().lower()
    return raw if raw in MODES else "enforce"


def mode_note() -> str | None:
    raw = os.environ.get("APP_BUILDER_CONTROL_GATE", DEFAULT_MODE).strip().lower()
    return None if raw in MODES else f"APP_BUILDER_CONTROL_GATE={raw!r} is not one of {MODES}: treated as enforce"


def max_evidence_age() -> int:
    try:
        return max(1, int(os.environ.get("APP_BUILDER_CONTROL_MAX_EVIDENCE_AGE", DEFAULT_MAX_EVIDENCE_AGE)))
    except ValueError:
        return DEFAULT_MAX_EVIDENCE_AGE


# ---------------------------------------------------------------- what the failure's evidence is
def _result_for(rec: Mapping[str, Any], app: str | None) -> dict | None:
    if not app:
        return None
    k = safe_id(app)
    for r in rec.get("qualification_results") or []:
        if isinstance(r, dict) and (r.get("app") == app or safe_id(r.get("app") or "") == k):
            return r
    return None


def identity_for(layer: str, item: Mapping[str, Any], rec: Mapping[str, Any]) -> AppIdentity:
    if layer == "qualify" and item.get("app"):
        r = _result_for(rec, item.get("app"))
        if r is None:
            return AppIdentity(kind="qualify", app=item.get("app"), build_id=rec.get("id"))
        return AppIdentity.from_result(r, build_id=rec.get("id"))
    return AppIdentity(kind="build", app=item.get("app"), build_id=rec.get("id"))


def evidence_for(layer: str, item: Mapping[str, Any], rec: Mapping[str, Any]) -> dict[str, Any]:
    """What the failure item rests on, as the build record holds it now. Fingerprinted by the gate; the same
    function applied to a fresh read of the record must give the same fingerprint for the repair to run."""
    ident = identity_for(layer, item, rec)
    ev: dict[str, Any] = {"layer": layer, "build_id": rec.get("id"), "build_state": rec.get("state"),
                          "item_key": item.get("key"), "app": item.get("app"),
                          "detail_sha256": sha256_bytes(str(item.get("detail") or "").encode("utf-8", "replace")),
                          "identity": ident.to_dict()}
    if layer == "qualify" and item.get("app"):
        r = _result_for(rec, item.get("app")) or {}
        v = r.get("verification") or {}
        ev.update(timestamp=v.get("finished_at") if v.get("finished_at") is not None else r.get("ts"),
                  verdict=r.get("verdict"), broken_at=r.get("broken_at"), code=r.get("code"),
                  verification_status=v.get("status"))
    elif layer == "handoff":
        c = rec.get("coolify") or {}
        st = (c.get("apps") or {}).get(item.get("app") or "", {}) if item.get("app") else {}
        ev.update(timestamp=c.get("last_attempt_at"), coolify_status=c.get("status"),
                  coolify_attempts=c.get("attempts"), app_status=st.get("status"))
    else:
        # When the build entered its current state (history), not `updated`: builds.update() bumps `updated`
        # on every write, including the healer saving its own progress, which would read as new evidence.
        entered = next((h.get("at") for h in reversed(rec.get("history") or [])
                        if isinstance(h, dict) and h.get("state") == rec.get("state")), None)
        ev.update(timestamp=entered, error_sha256=sha256_bytes(str(rec.get("error") or "").encode()))
    return ev


def remediation_id(layer: str, build_id: str | None, failure_key: str, app: str | None, action: str,
                   args: Mapping[str, Any]) -> str:
    """Deterministic: the same repair for the same failure of the same build is the same remediation, so a
    person can approve the id a refusal printed and the next healing round finds that approval."""
    return "rem-" + fingerprint({"layer": layer, "build_id": build_id, "failure_key": failure_key,
                                 "app": app or None, "action": action, "args": dict(args)})[:20]


def _redacted(value: Any) -> Any:
    """ATTa's own secret redaction (redact.py) over every string an audit event would store: repair arguments,
    results and exception text can carry configuration values. The remediation id and fingerprints are
    computed from the unredacted values, so redaction never changes what is being approved."""
    if isinstance(value, str):
        return redact(value)
    if isinstance(value, Mapping):
        return {k: _redacted(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_redacted(v) for v in value]
    return value


# ---------------------------------------------------------------- approvals
def requested(store: EventStore, rem_id: str) -> dict | None:
    """The latest time the gate was asked about this remediation: what failure, what action, what arguments."""
    return next((e for e in reversed(store.events(store.AUDIT))
                 if e.get("event") == "REPAIR_AUTHORIZATION" and e.get("remediation_id") == rem_id), None)


def approve(store: EventStore, rem_id: str, approved_by: str, ttl: int | None = None, note: str = "") -> dict:
    """Record a person's approval for one remediation the gate has asked about. The approval names what it
    covers (failure key, action, arguments, app, build), so the log shows what was approved, not just an id."""
    import re
    if not re.fullmatch(r"rem-[0-9a-f]{20}", rem_id or ""):
        raise ValueError(f"{rem_id!r} is not a remediation id (rem- followed by 20 hex characters)")
    if not (approved_by or "").strip():
        raise ValueError("an approval must name the person approving it")
    if ttl is not None and ttl <= 0:
        raise ValueError("an approval's lifetime must be a positive number of seconds")
    asked = requested(store, rem_id)
    if asked is None:
        raise ValueError(f"no repair with id {rem_id} has been asked for: approvals cover a refusal the gate recorded")
    now = time.time()
    return store.append(store.APPROVALS, {"event": "APPROVED", "remediation_id": rem_id,
                                          "approved_by": approved_by.strip(), "note": note,
                                          "covers": {k: asked.get(k) for k in ("layer", "failure_key", "fix_name", "args",
                                                                               "decision", "policy_version")}
                                                    | {"app": (asked.get("identity") or {}).get("app"),
                                                       "build_id": (asked.get("identity") or {}).get("build_id")},
                                          "approved_at": now, "expires_at": now + (ttl or DEFAULT_APPROVAL_TTL)})


def approval_state(store: EventStore, rem_id: str, now: float | None = None) -> dict | None:
    """The approval that is usable for rem_id right now (approved, not expired, not yet consumed), or None."""
    now = time.time() if now is None else now
    live: dict | None = None
    for e in store.events(store.APPROVALS):
        if e.get("remediation_id") != rem_id:
            continue
        if e.get("event") == "APPROVED":
            live = e
        elif e.get("event") == "CONSUMED" and live is not None and e.get("approval_seq") == live.get("seq"):
            live = None
    if live is None or float(live.get("expires_at") or 0) <= now:
        return None
    return live


# ---------------------------------------------------------------- revisions a recorded repair produced
_OWN_CHANGES: dict[tuple[str, str], set[str]] = {}
_OWN_LOCK = threading.Lock()


_OWN_MAX = 2000   # jobs remembered; the oldest are dropped (a healing round only needs its own job's)


def _note_own_change(build_id: str | None, run_id: str | None, app_rev_after: str | None) -> None:
    if build_id and run_id and app_rev_after:
        with _OWN_LOCK:
            _OWN_CHANGES.setdefault((build_id, run_id), set()).add(app_rev_after)
            while len(_OWN_CHANGES) > _OWN_MAX:
                _OWN_CHANGES.pop(next(iter(_OWN_CHANGES)))


def _explained_by_own_repair(build_id: str | None, run_id: str | None, now_rev: str) -> bool:
    with _OWN_LOCK:
        return now_rev in _OWN_CHANGES.get((build_id or "", run_id or ""), set())


def _current_revisions(app: str) -> tuple[str, str]:
    """(deployment revision, app revision) as they are right now, by resilience.identity."""
    import builds
    from resilience import app_revision, deployment_revision
    dep = deployment_revision(builds.ROOT)
    try:
        import app_runner
        rev = app_revision(app_runner.app_dir(app))
    except (FileNotFoundError, OSError):
        rev = "missing"
    return dep, rev


# ---------------------------------------------------------------- the decision
def authorize(*, layer: str, item: Mapping[str, Any], rec: Mapping[str, Any], action: str,
              args: Mapping[str, Any], tier: Any = None, rule: Any = None, store: EventStore,
              policy: RepairPolicy | None = None, reread: Callable[[], Mapping[str, Any] | None] | None = None,
              revisions: Callable[[str], tuple[str, str]] | None = None, gate_mode: str | None = None,
              now: float | None = None) -> RepairAuthorization:
    """Every check, every time, in every mode (observe records what enforce would have done). Writes one
    REPAIR_AUTHORIZATION event. Raises only if that event cannot be written."""
    gate_mode = gate_mode or mode()
    now = time.time() if now is None else now
    reasons: list[str] = []
    fk = failure_keys.make(layer, str(item.get("key") or ""), str(item.get("detail") or ""))
    ident = identity_for(layer, item, rec)
    reasons += ident.problems()

    evidence = evidence_for(layer, item, rec)
    ev_fp = fingerprint(evidence)
    ts = evidence.get("timestamp")
    if not isinstance(ts, (int, float)) or isinstance(ts, bool):
        reasons.append("evidence timestamp missing or not a number")
    elif now - float(ts) > max_evidence_age():
        reasons.append(f"evidence is stale: recorded {int(now - float(ts))}s ago (limit {max_evidence_age()}s)")
    elif float(ts) > now + FUTURE_SKEW:
        reasons.append("evidence timestamp is in the future")

    if reread is not None:
        try:
            fresh = reread()
        except Exception as e:
            fresh, err = None, f"{type(e).__name__}: {e}"
        else:
            err = None
        if fresh is None:
            reasons.append("the build record could not be re-read at the gate" + (f" ({err})" if err else ""))
        elif fingerprint(evidence_for(layer, item, fresh)) != ev_fp:
            reasons.append("the build record changed after the self-healer read it: the evidence is not current")

    if ident.kind == "qualify" and not ident.problems():
        try:
            dep_now, app_now = (revisions or _current_revisions)(ident.app or "")
        except Exception as e:
            reasons.append(f"current revisions could not be read ({type(e).__name__}: {e})")
        else:
            if dep_now != ident.deployment_revision:
                reasons.append(f"ATTa deployment changed since the failure was observed ({ident.deployment_revision} -> {dep_now})")
            if app_now != ident.app_revision and not _explained_by_own_repair(rec.get("id"), ident.run_id, app_now):
                reasons.append(f"the app changed since the failure was observed ({ident.app_revision} -> {app_now}) "
                               "and no repair recorded by this gate explains it")

    rem = remediation_id(layer, rec.get("id"), fk, item.get("app"), action, args)
    policy_version = "unloaded"
    decision = "ESCALATE"
    try:
        policy = policy if policy is not None else load_policy()
        d = policy.decide(fk, action)
        decision, policy_version = d.action, d.policy_version
        policy_note = d.reason
    except PolicyError as e:
        policy_note = f"policy could not be trusted: {e}"
        reasons.append(policy_note)
    approval = None
    if decision == "APPROVAL_REQUIRED":
        approval = approval_state(store, rem, now)
        if approval is None:
            reasons.append(f"human approval required: python3 -m atta_control approve {rem} --by <name>")
    elif decision != "AUTO":
        reasons.append(f"policy decision {decision} does not permit running {action}")
    if failure_keys.is_unknown(fk) and decision in ("AUTO", "APPROVAL_REQUIRED"):
        reasons.append("failure key is UNKNOWN_FAILURE: no policy can authorize a repair for it")   # defence in depth
    note = mode_note()
    if note:
        reasons.append(note)

    allowed = not reasons
    auth = RepairAuthorization(allowed=allowed, action=decision, policy_version=policy_version, remediation_id=rem,
                               reasons=tuple(reasons) if reasons else (policy_note,),
                               evidence_fingerprint=ev_fp, mode=gate_mode)
    store.append_audit({"event": "REPAIR_AUTHORIZATION", "mode": gate_mode, "authorized": allowed,
                        "would_block": not allowed, "layer": layer, "tier": tier, "rule": rule,
                        "failure_key": fk, "fix_name": action, "args": _redacted(dict(args)), "remediation_id": rem,
                        "policy_version": policy_version, "decision": decision, "reasons": list(auth.reasons),
                        "identity": ident.to_dict(), "evidence": evidence, "evidence_fingerprint": ev_fp,
                        "approval_seq": approval.get("seq") if approval else None, "at": now})
    if allowed and approval is not None and gate_mode == "enforce":
        store.append(store.APPROVALS, {"event": "CONSUMED", "remediation_id": rem, "approval_seq": approval.get("seq"),
                                       "at": now})
    return auth


def execute(ctx: Mapping[str, Any] | None, name: str, args: Mapping[str, Any],
            runner: Callable[[str, dict], Any], *, store: EventStore | None = None,
            policy: RepairPolicy | None = None, revisions: Callable[[str], tuple[str, str]] | None = None) -> Any:
    """Run one existing repair action through the gate. `runner` is repair_actions.run; `ctx` is what
    maintenance.py knows about the failure: {"layer", "item", "rec", "tier", "rule"}.

    off: runner(name, args), nothing else. observe: record everything, run regardless (a failure of the
    gate itself never stops a repair). enforce: run only when authorize() allows it; a gate that cannot
    decide or cannot record is a refusal."""
    gate_mode = mode()
    if gate_mode == "off" or ctx is None:
        return runner(name, dict(args))
    store = store or EventStore()
    layer, item, rec = ctx.get("layer"), ctx.get("item") or {}, ctx.get("rec") or {}

    def reread():
        import builds
        return builds.get(rec.get("id")) if rec.get("id") else None

    try:
        auth = authorize(layer=layer, item=item, rec=rec, action=name, args=args, tier=ctx.get("tier"),
                         rule=ctx.get("rule"), store=store, policy=policy, reread=reread, revisions=revisions,
                         gate_mode=gate_mode)
    except Exception as e:
        if gate_mode == "enforce":
            raise RepairDenied(f"repair gate could not decide or record ({type(e).__name__}: {e}); refused") from e
        print(f"atta_control gate (observe): {type(e).__name__}: {e}; repair runs unaudited", flush=True)
        return runner(name, dict(args))
    if not auth.allowed and gate_mode == "enforce":
        raise RepairDenied(f"repair gate refused {name} [{auth.remediation_id}]: " + "; ".join(auth.reasons))

    ident = identity_for(layer, item, rec)
    base = {"remediation_id": auth.remediation_id, "fix_name": name, "mode": gate_mode, "layer": layer,
            "identity": ident.to_dict(), "evidence_fingerprint": auth.evidence_fingerprint}
    _audit_or_raise(store, {**base, "event": "REPAIR_STARTED", "authorized": auth.allowed, "at": time.time()},
                    enforce=gate_mode == "enforce")
    try:
        out = runner(name, dict(args))
    except Exception as e:
        _audit_quietly(store, {**base, "event": "REPAIR_EXCEPTION", "exception_type": type(e).__name__,
                               "exception": redact(str(e)[:1000]), "at": time.time()})
        raise
    after = {}
    if ident.kind == "qualify" and ident.app:
        try:
            dep_after, app_after = (revisions or _current_revisions)(ident.app)
            after = {"deployment_revision_after": dep_after, "app_revision_after": app_after}
            _note_own_change(rec.get("id"), ident.run_id, app_after)
        except Exception as e:
            after = {"revisions_after_error": f"{type(e).__name__}: {e}"}
    _audit_quietly(store, {**base, "event": "REPAIR_RETURNED", "result_sha256": sha256_bytes(str(out).encode("utf-8", "replace")),
                           "result": redact(str(out)[:500]), **after, "at": time.time()})
    return out


def _audit_or_raise(store: EventStore, event: dict, enforce: bool) -> None:
    try:
        store.append_audit(event)
    except Exception as e:
        if enforce:
            raise RepairDenied(f"repair not started: the audit log could not be written ({type(e).__name__}: {e})") from e
        print(f"atta_control gate: audit not written ({type(e).__name__}: {e})", flush=True)


def _audit_quietly(store: EventStore, event: dict) -> None:
    """After a repair ran, an audit failure can no longer stop it: report it and carry on."""
    try:
        store.append_audit(event)
    except Exception as e:
        print(f"atta_control gate: audit not written for {event.get('event')} ({type(e).__name__}: {e})", flush=True)
