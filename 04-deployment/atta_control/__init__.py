"""
atta_control — the control layer over ATTa's existing runner, self-healer, diagnostic and Coolify hand-off. v121.4.

Additive by construction. It never starts, stops, qualifies or deploys an app, and never decides a verdict:
app_runner.py, system_watcher.py, pipeline.py, maintenance.py and coolify_handoff.py stay authoritative.
What it adds:

  store.py     an append-only, hash-chained audit log (state/control/*.jsonl): one line per event, each line
               carrying the SHA-256 of the line before it, so a deleted or edited line is detectable.
  policy.py    a versioned, explicit repair policy: (FailureKey, repair action) -> AUTO / APPROVAL_REQUIRED /
               RETRY_ONLY / ESCALATE / NO_ACTION. A pair the policy does not name is ESCALATE. Fail closed.
  gate.py      the repair gate the self-healer calls before every repair action: identity complete, evidence
               fresh, the app and the deployment unchanged since the failure was observed, policy allows it,
               approval (when required) granted for this exact remediation. Mode off / observe / enforce
               (APP_BUILDER_CONTROL_GATE, default observe: audit everything, block nothing).
  records.py   reads ATTa's real per-app results into one observation contract, and exports each fleet run's
               failures as state/runner/failures/<fleet run>/<app>/<run id>-a<attempt>.json: the tree the
               v121.3 record diagnostic reads and nothing in v121.3 wrote.
  deploy.py    per-app Coolify deployment state read from the build records, plus an optional live HTTP
               probe of each app's Coolify URL. "Coolify accepted it" is never reported as "it is live".
  report.py    the control report (state/runner/control/CONTROL-REPORT.{json,md}), built only from persisted
               records: missing data is shown as UNKNOWN, never filled in. The fleet report is not touched.

Command line: cd 04-deployment && python3 -m atta_control --help
"""
from .models import AppIdentity, DeploymentState, EvidenceRef, Observation, RepairAuthorization, STAGES
from .policy import ACTIONS, PolicyDecision, PolicyError, RepairPolicy, load_policy
from .store import EventStore, canonical_json_bytes, fingerprint, verify_chain

__all__ = ["AppIdentity", "DeploymentState", "EvidenceRef", "Observation", "RepairAuthorization", "STAGES",
           "ACTIONS", "PolicyDecision", "PolicyError", "RepairPolicy", "load_policy",
           "EventStore", "canonical_json_bytes", "fingerprint", "verify_chain"]
