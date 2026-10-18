"""ATTa resilience boundary: safe observation, deployment/app identity, outcome vocabulary and the fleet
report. Existing deployd, watcher and lifecycle code remain authoritative."""
from .identity import (deployment_revision, code_revision, app_revision, new_correlation_id,
                       verification_snapshot, revision_stable)
from .probes import safe_http_probe, safe_tcp_probe
from .states import STATES, handoff_state, levels, passed_all_stages
from .report import build_report, report_markdown, write_report

__all__ = ["deployment_revision", "code_revision", "app_revision", "new_correlation_id", "verification_snapshot",
           "revision_stable", "safe_http_probe", "safe_tcp_probe", "STATES", "handoff_state", "levels",
           "passed_all_stages", "build_report", "report_markdown", "write_report"]
