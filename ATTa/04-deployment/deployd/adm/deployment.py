"""The deployment record and its state machine (v117, checklist C).

Every deployment — an ADM job, or `sudo bash run` typed by hand — has one record (its journal doc) with:

  deployment_id, kind (adm|manual), state + state_history, created/started/completed timestamps,
  source (bundle sha256, source commit, code sha256), previous_live (release, version, verified?),
  attempted (version, release), failure_reason, process (pid, pgid, host), rollback (needed/result/target),
  final_live (release, version).

States and the only transitions allowed:

    created -> validating -> building -> built -> health_checking -> verified -> live
       any of those (not live) -> failed | timed_out | interrupted
       failed | timed_out | interrupted -> rolled_back          (only when the rollback was verified)

`live` is reachable ONLY from `verified`, and `verified` only from `health_checking`: a deployment that failed,
timed out, was interrupted or never finished its checks can never be recorded as live. Illegal transitions raise
IllegalTransition and change nothing.
"""
from __future__ import annotations
import os, socket
from . import journal

STATES = ("created", "validating", "building", "built", "health_checking", "verified", "live",
          "failed", "timed_out", "interrupted", "rolled_back")
FAILURES = ("failed", "timed_out", "interrupted")
_IN_PROGRESS = ("created", "validating", "building", "built", "health_checking", "verified")
TRANSITIONS = {
    "created": {"validating"},
    "validating": {"building"},
    "building": {"built"},
    "built": {"health_checking"},
    "health_checking": {"verified"},
    "verified": {"live"},
    "live": set(),
    "failed": {"rolled_back"},
    "timed_out": {"rolled_back"},
    "interrupted": {"rolled_back"},
    "rolled_back": set(),
}
for _s in _IN_PROGRESS:
    TRANSITIONS[_s] |= set(FAILURES)
# Rollback results.
NOT_NEEDED, PENDING, SUCCEEDED, ROLLBACK_FAILED, BLOCKED = "not_needed", "pending", "succeeded", "failed", "blocked"


class IllegalTransition(RuntimeError):
    pass


def is_final(doc) -> bool:
    """No more changes expected: live, rolled_back, or a failure whose rollback is settled."""
    st = doc.get("state")
    if st in ("live", "rolled_back"):
        return True
    return st in FAILURES and (doc.get("rollback") or {}).get("result") in (NOT_NEEDED, ROLLBACK_FAILED, BLOCKED)


def verdict_of(doc) -> str:
    """The one-word result shown by deployctl and the build page (kept compatible with v114-v116 words)."""
    st = doc.get("state")
    rb = (doc.get("rollback") or {}).get("result")
    if st == "live":
        return "DEPLOYED"
    if st == "rolled_back":
        return "ROLLED_BACK"
    if st in FAILURES:
        if rb in (ROLLBACK_FAILED, BLOCKED):
            return "ROLLBACK_FAILED"
        return {"failed": "FAILED", "timed_out": "TIMED_OUT", "interrupted": "INTERRUPTED"}[st]
    return (st or "queued").upper()


def create(deployment_id, *, kind, origin=None, requested_by=None, source=None, previous_live=None,
           attempted=None, **extra):
    """Open the record in state `created`. Refuses to overwrite an existing v117 record."""
    def apply(doc):
        if "state" in doc:
            raise IllegalTransition(f"deployment {deployment_id} already exists (state {doc['state']})")
        at = journal.now()
        doc.update({"deployment_id": deployment_id, "kind": kind, "state": "created",
                    "state_history": [{"state": "created", "at": at}], "started_at": at, "completed_at": None,
                    "source": source or {}, "previous_live": previous_live, "attempted": attempted or {},
                    "failure_reason": None, "process": {"host": socket.gethostname()}, "rollback": None,
                    "final_live": None})
        if origin is not None:
            doc["origin"] = origin
        if requested_by is not None:
            doc["requested_by"] = requested_by
        doc.update(extra)
        doc["verdict"] = verdict_of(doc)
        doc["events"].append({"at": at, "event": "CREATED", "kind": kind})
    return journal.update(deployment_id, apply)


def transition(deployment_id, state, *, reason=None, **fields):
    """Move to `state` if the state machine allows it; record why. Returns the doc."""
    if state not in STATES:
        raise IllegalTransition(f"unknown state {state!r}")

    def apply(doc):
        cur = doc.get("state")
        if cur is None:
            raise IllegalTransition(f"deployment {deployment_id} has no record")
        if state not in TRANSITIONS[cur]:
            raise IllegalTransition(f"deployment {deployment_id}: {cur} -> {state} is not allowed")
        if state == "rolled_back" and (doc.get("rollback") or {}).get("result") != SUCCEEDED:
            raise IllegalTransition(f"deployment {deployment_id}: rolled_back needs a verified rollback first")
        at = journal.now()
        doc["state"] = state
        doc["state_history"].append({"state": state, "at": at, **({"reason": reason} if reason else {})})
        if state in FAILURES and reason:
            doc["failure_reason"] = reason
        for k, v in fields.items():
            doc[k] = v
        if is_final(doc) or state == "live":
            doc["completed_at"] = at
        doc["verdict"] = verdict_of(doc)
        doc["events"].append({"at": at, "event": state.upper(), **({"reason": reason} if reason else {})})
    return journal.update(deployment_id, apply)


def set_fields(deployment_id, **fields):
    """Record facts (pid/pgid, attempted release, ...) without changing state. Dict fields are merged."""
    def apply(doc):
        if "state" not in doc:
            raise IllegalTransition(f"deployment {deployment_id} has no record")
        for k, v in fields.items():
            if isinstance(v, dict) and isinstance(doc.get(k), dict):
                doc[k] = {**doc[k], **v}
            else:
                doc[k] = v
    return journal.update(deployment_id, apply)


def set_rollback(deployment_id, result, **fields):
    """Record the rollback's outcome (needed? which target? verified?). Settles the verdict when final."""
    def apply(doc):
        if doc.get("state") not in FAILURES + ("rolled_back",):
            raise IllegalTransition(f"deployment {deployment_id}: rollback recorded while {doc.get('state')}")
        doc["rollback"] = {**(doc.get("rollback") or {}), "result": result, "at": journal.now(), **fields}
        if is_final(doc):
            doc["completed_at"] = doc["completed_at"] or journal.now()
        doc["verdict"] = verdict_of(doc)
        doc["events"].append({"at": journal.now(), "event": f"ROLLBACK_{result.upper()}",
                              **({"reason": fields["reason"]} if fields.get("reason") else {})})
    return journal.update(deployment_id, apply)


def get(deployment_id):
    return journal.load(deployment_id) if journal.exists(deployment_id) else None


def unfinished():
    """Records still in progress (or failed with the rollback not settled): after a crash, these were interrupted."""
    return [d for d in journal.all_jobs() if "state" in d and not is_final(d)]


def process_fields(pid, pgid):
    return {"process": {"pid": pid, "pgid": pgid, "host": socket.gethostname(), "runner_pid": os.getpid()}}
