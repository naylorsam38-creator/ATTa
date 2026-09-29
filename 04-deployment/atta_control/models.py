"""The shared contract of the control layer: who a piece of evidence belongs to, what was observed, and what
the gate decided. Every field is read from an ATTa record; a field ATTa did not record stays None and is
reported as missing, never guessed.

Outcomes are ATTa's own (resilience.states.STATES); this module does not define a second vocabulary."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Mapping, Optional

from resilience.states import STATES as OUTCOMES  # noqa: F401  (re-exported: the one outcome vocabulary)

# The six watcher stages, in order, exactly as system_watcher.py names them.
STAGES = ("1 INSTALLED", "2 APP_UP", "3 PROXY_UP", "4 SKIN", "5 HOOK", "6 CLEAN")
# The three layers the self-healer works on (maintenance.LAYERS).
LAYERS = ("build", "qualify", "handoff")
# Revision values resilience.identity writes when it could not read the thing it identifies.
UNREADABLE_REVISIONS = ("", "unknown", "missing")


class DeploymentState(str, Enum):
    """Per-app Coolify state. Only a live probe made now can say LIVE_HTTP_OK; Coolify accepting a deploy
    request is DISPATCHED, which says nothing about whether the app came up."""
    NOT_REQUESTED = "NOT_REQUESTED"   # the app's build never reached the hand-off
    PENDING = "PENDING"               # hand-off written, deploy call not yet made
    BLOCKED = "BLOCKED"               # Coolify not configured / resource unmapped / unprovisionable
    DISPATCH_ERROR = "DISPATCH_ERROR" # the deploy call failed (retried by coolify_handoff)
    DISPATCHED = "DISPATCHED"         # Coolify accepted the deploy request (queued or service started)
    LIVE_HTTP_OK = "LIVE_HTTP_OK"     # probed now: the app's Coolify URL answered with a non-5xx HTTP status
    LIVE_FAILED = "LIVE_FAILED"       # probed now: no answer, or a 5xx
    UNKNOWN = "UNKNOWN"               # dispatched, but no URL recorded to probe, or probing not requested


@dataclass(frozen=True)
class AppIdentity:
    """Who a result belongs to. `kind` is "qualify" for one app's qualification job (all of run_id, attempt,
    correlation_id and both revisions come from the job) or "build" for a build/hand-off failure, which is
    identified by its build record rather than an app job."""
    kind: str
    app: Optional[str]
    build_id: Optional[str] = None
    fleet_run_id: Optional[str] = None
    run_id: Optional[str] = None
    attempt: Optional[int] = None
    correlation_id: Optional[str] = None
    deployment_revision: Optional[str] = None
    app_revision: Optional[str] = None

    def problems(self) -> list[str]:
        """What is missing for this identity to authorize anything. Empty = complete."""
        out: list[str] = []
        if self.kind not in ("qualify", "build"):
            out.append(f"unknown identity kind {self.kind!r}")
            return out
        if self.kind == "build":
            if not (self.build_id or "").strip():
                out.append("missing build id")
            return out
        if not (self.app or "").strip():
            out.append("missing app")
        if not (self.run_id or "").strip():
            out.append("missing run id")
        if not isinstance(self.attempt, int) or isinstance(self.attempt, bool) or self.attempt < 1:
            out.append("attempt missing or < 1")
        if not (self.correlation_id or "").strip():
            out.append("missing correlation id")
        if (self.deployment_revision or "") in UNREADABLE_REVISIONS:
            out.append("deployment revision missing or unreadable")
        if (self.app_revision or "") in UNREADABLE_REVISIONS:
            out.append("app revision missing or unreadable")
        return out

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_result(cls, result: Mapping[str, Any], build_id: Optional[str] = None) -> "AppIdentity":
        """The identity app_runner stamped on a result (its `verification` block). Nothing is inferred:
        a result written before v121.2, or a contained crash, simply has less identity."""
        v = result.get("verification") or {}
        attempt = v.get("attempt")
        return cls(kind="qualify", app=result.get("app"), build_id=build_id,
                   fleet_run_id=v.get("fleet_run_id"), run_id=v.get("run_id"),
                   attempt=attempt if isinstance(attempt, int) and not isinstance(attempt, bool) else None,
                   correlation_id=v.get("correlation_id"), deployment_revision=v.get("deployment_revision"),
                   app_revision=v.get("app_revision"))


@dataclass(frozen=True)
class EvidenceRef:
    """A file that backs an observation, pinned by its content hash at the time it was read."""
    path: str
    sha256: str
    source: str
    stage: Optional[str]
    captured_at: Optional[float]


@dataclass(frozen=True)
class Observation:
    """One stage of one app's job as ATTa recorded it. `result` is the stage status as written (OK, FAIL,
    SKIPPED, NOT_RUN, ...); `summary` is the stage code and detail, truncated. Never a cause."""
    identity: AppIdentity
    stage: str
    result: str
    code: Optional[str]
    summary: str
    observed_at: Optional[float]
    source: str
    evidence: tuple[EvidenceRef, ...] = ()
    details: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["evidence"] = [asdict(e) for e in self.evidence]
        return d


@dataclass(frozen=True)
class RepairAuthorization:
    """The gate's answer for one repair action. `allowed` is what enforce mode obeys; observe mode records
    it and runs the repair anyway."""
    allowed: bool
    action: str
    policy_version: str
    remediation_id: str
    reasons: tuple[str, ...]
    evidence_fingerprint: str
    mode: str
