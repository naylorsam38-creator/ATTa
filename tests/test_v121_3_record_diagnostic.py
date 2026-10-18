import json
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "04-deployment"))
import record_diagnostic as rd


def write_record(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj), encoding="utf-8")


def test_missing_copy_is_observation_not_atta_root_cause(tmp_path):
    p = tmp_path / "x" / "record.json"
    write_record(p, {
        "app": "open-design",
        "run_id": "r1",
        "revision": "code:a",
        "attempt": 1,
        "status": "START_FAILED",
        "error": 'COPY plugins/_official ./plugins/_official: not found',
    })
    before = rd.audit_input_fingerprints([p])
    r = rd.classify(p)
    assert r["classification"] == "BUILD_CONTEXT_SOURCE_MISSING"
    assert r["root_cause"]["value"] == "UNKNOWN"
    assert r["allowed_action"] == "ESCALATE"
    assert r["remediation_id"] is None
    assert rd.fingerprints_unchanged(before)


def test_502_does_not_become_known_root_cause(tmp_path):
    p = tmp_path / "x" / "record.json"
    write_record(p, {
        "app": "appsmith", "run_id": "r1", "revision": "code:a", "attempt": 1,
        "status": "FAILED", "browser": {"error": "502 Bad Gateway"}
    })
    r = rd.classify(p)
    assert r["classification"] == "UPSTREAM_502"
    assert r["root_cause"]["value"] == "UNKNOWN"
    assert r["allowed_action"] == "ESCALATE"


def test_missing_identity_is_no_action(tmp_path):
    p = tmp_path / "x" / "record.json"
    write_record(p, {"app": "memos", "status": "FAILED", "error": "ERR_NETWORK_CHANGED"})
    r = rd.classify(p)
    assert r["classification"] == "EVIDENCE_IDENTITY_INCOMPLETE"
    assert r["allowed_action"] == "NO_ACTION"


def test_mismatched_related_identity_is_no_action(tmp_path):
    p = tmp_path / "x" / "record.json"
    q = tmp_path / "x" / "browser.json"
    base = {"app":"memos","run_id":"r1","revision":"code:a","attempt":1,"error":"ERR_NETWORK_CHANGED"}
    write_record(p, base)
    write_record(q, {"app":"memos","run_id":"r2","revision":"code:a","attempt":1})
    r = rd.classify(p, [q])
    assert r["classification"] == "EVIDENCE_IDENTITY_MISMATCH"
    assert r["allowed_action"] == "NO_ACTION"


def test_protocol_mismatch_needs_explicit_evidence(tmp_path):
    p = tmp_path / "x" / "record.json"
    write_record(p, {
        "app":"supabase","run_id":"r1","revision":"code:a","attempt":1,
        "error":"TCP connection succeeded; plain HTTP sent to HTTPS port"
    })
    r = rd.classify(p)
    assert r["classification"] == "HTTP_PROTOCOL_MISMATCH"
    assert r["root_cause"]["value"] == "UNKNOWN"


def test_duplicate_records_cannot_authorize_action(tmp_path):
    write_record(tmp_path/"memos"/"a.json",
                 {"app":"memos","run_id":"r1","revision":"a","attempt":1})
    write_record(tmp_path/"memos"/"b.json",
                 {"app":"memos","run_id":"r1","revision":"a","attempt":1})
    out = rd.run(tmp_path, ["memos"])
    assert out["findings"][0]["classification"] == "APP_IDENTITY_DUPLICATE"
    assert out["findings"][0]["allowed_action"] == "NO_ACTION"


def test_policy_actions_are_closed_set():
    assert set(v["allowed_action"] for v in rd.POLICY.values()) <= rd.ALLOWED_ACTIONS


def test_real_22_record_regression_requires_real_records_when_enabled():
    """
    Set ATTA_RECORD_DIAGNOSTIC_ROOT to the actual state/runner/failures directory
    to run the real 22-record regression. Without it, this test deliberately does
    not pretend the real records were present.
    """
    root = os.environ.get("ATTA_RECORD_DIAGNOSTIC_ROOT")
    if not root:
        default = Path("/srv/app-builder/state/runner/failures")
        root = str(default) if default.is_dir() else None
    if not root:
        pytest.skip("real ATTa failure-record directory not mounted in this package run")
    manifest = json.loads(
        (Path(__file__).parent / "fixtures" / "v121_3_22_failure_manifest.json").read_text()
    )
    out = rd.run(Path(root), manifest["apps"])
    assert out["expected_count"] == 22
    assert out["missing_apps"] == [], out["missing_apps"]
    assert out["processed_count"] == 22
    for finding in out["findings"]:
        assert finding["allowed_action"] in rd.ALLOWED_ACTIONS
        assert finding["root_cause"]["value"] == "UNKNOWN" or finding["root_cause"].get("proof")
        assert finding["evidence"], finding
        assert finding["remediation_id"] is None
