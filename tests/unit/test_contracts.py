"""Unit tests for backend/contracts, settings and the policy/task primitives."""
from __future__ import annotations

import json
import logging
import sys
from datetime import datetime
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from backend.contracts import (ALLOWED_TRANSITIONS, DependencyHealth, HealthStatus, IdempotencyConflict,  # noqa: E402
                               InMemoryIdempotencyStore, InvalidTransition, PageRequest, ReadinessReport, TaskState,
                               assert_transition, get_correlation_id, is_valid_id, iso_utc, new_id, paginate,
                               parse_id, request_fingerprint, set_correlation_id, to_utc)
from backend.contracts.logging import JsonFormatter  # noqa: E402
from backend.settings import ConfigurationError, load_settings  # noqa: E402
from backend.task_manager import TaskStore  # noqa: E402


def test_identifiers_are_prefixed_sortable_and_validated():
    a, b = new_id("tsk"), new_id("tsk")
    assert is_valid_id(a, "tsk") and not is_valid_id(a, "usr") and a != b
    prefix, ms = parse_id(a)
    assert prefix == "tsk" and abs(ms / 1000 - datetime.now().timestamp()) < 5
    with pytest.raises(ValueError):
        new_id("bad")
    with pytest.raises(ValueError):
        parse_id("tsk_notvalid")


def test_timestamps_are_utc_and_naive_rejected():
    assert iso_utc().endswith("Z")
    with pytest.raises(ValueError):
        to_utc(datetime(2026, 1, 1))


def test_pagination_round_trip_and_bad_cursor():
    items = list(range(7))
    p1 = paginate(items, PageRequest(limit=3))
    p2 = paginate(items, PageRequest(limit=3, cursor=p1.next_cursor))
    p3 = paginate(items, PageRequest(limit=3, cursor=p2.next_cursor))
    assert p1.items + p2.items + p3.items == items and p3.next_cursor is None and p1.total == 7
    with pytest.raises(ValueError):
        paginate(items, PageRequest(cursor="!!garbage"))
    with pytest.raises(Exception):
        PageRequest(limit=10_000)


def test_idempotency_same_request_replays_different_request_conflicts():
    clock = [0.0]
    s = InMemoryIdempotencyStore(ttl_seconds=10, clock=lambda: clock[0])
    fp = request_fingerprint("POST", "/v1/x", {"a": 1})
    assert s.lookup("u", "k1", fp) is None
    s.store("u", "k1", fp, 200, {"ok": True})
    assert s.lookup("u", "k1", fp).body == {"ok": True}
    with pytest.raises(IdempotencyConflict):
        s.lookup("u", "k1", request_fingerprint("POST", "/v1/x", {"a": 2}))
    assert s.lookup("other-user", "k1", fp) is None, "keys are scoped per principal"
    clock[0] = 11
    assert s.lookup("u", "k1", fp) is None, "expired keys are forgotten"


def test_correlation_ids_reject_injection():
    assert set_correlation_id("abc-123-valid_ID") == "abc-123-valid_ID"
    cid = set_correlation_id("bad\nvalue injected")
    assert cid.startswith("cor_") and get_correlation_id() == cid


def test_json_logging_redacts_secrets():
    rec = logging.makeLogRecord({"msg": "hello", "levelname": "INFO", "name": "t", "password": "p@ss",
                                 "nested": {"api_key": "sk-123", "ok": 1}})
    out = json.loads(JsonFormatter().format(rec))
    assert out["fields"]["password"] == "[REDACTED]" and out["fields"]["nested"]["api_key"] == "[REDACTED]"
    assert out["fields"]["nested"]["ok"] == 1 and out["ts"].endswith("Z")


def test_task_state_machine():
    assert_transition(TaskState.PENDING, TaskState.QUEUED)
    with pytest.raises(InvalidTransition):
        assert_transition(TaskState.SUCCEEDED, TaskState.RUNNING)
    for terminal in (TaskState.SUCCEEDED, TaskState.FAILED, TaskState.CANCELLED, TaskState.TIMED_OUT):
        assert ALLOWED_TRANSITIONS[terminal] == frozenset()
    store = TaskStore()
    t = store.create("demo", "usr_x")
    for s in (TaskState.QUEUED, TaskState.RUNNING, TaskState.VERIFYING, TaskState.SUCCEEDED):
        store.transition(t.id, s)
    assert [e.data["to"] for e in store.get(t.id).events] == ["queued", "running", "verifying", "succeeded"]
    with pytest.raises(InvalidTransition):
        store.transition(t.id, TaskState.QUEUED)


def test_readiness_aggregation():
    ok = DependencyHealth(name="pg", essential=True, status=HealthStatus.HEALTHY)
    opt = DependencyHealth(name="s3", essential=False, status=HealthStatus.UNHEALTHY)
    bad = DependencyHealth(name="redis", essential=True, status=HealthStatus.UNHEALTHY, detail="refused")
    assert ReadinessReport.from_dependencies("t", [ok]).status is HealthStatus.HEALTHY
    r = ReadinessReport.from_dependencies("t", [ok, opt])
    assert r.ready and r.status is HealthStatus.DEGRADED
    r = ReadinessReport.from_dependencies("t", [ok, opt, bad])
    assert not r.ready and r.status is HealthStatus.UNHEALTHY and "redis" in r.reasons[0]


def test_settings_layering_env_overrides_and_secret_rules(tmp_path):
    s = load_settings("test", env={})
    assert s.api.port == 8810 and s.redis.port == 6390 and s.environment.value == "test"
    s = load_settings("test", env={"ATTA__API__PORT": "9123", "ATTA_MODEL_PATHS": "/mnt/usb/models"})
    assert s.api.port == 9123 and s.models.storage_paths == ["/mnt/usb/models"]
    assert any("not currently present" in w for w in s.warnings())
    with pytest.raises(ConfigurationError, match="unknown environment"):
        load_settings("nope", env={})
    with pytest.raises(ConfigurationError, match="production requires database.password"):
        load_settings("production", env={}, include_local_env=False)
    with pytest.raises(ConfigurationError):
        load_settings("test", env={"ATTA__API__PORT": "70000"})
    # Secrets in tracked YAML layers are refused.
    root = tmp_path / "repo"
    (root / "configuration/environments").mkdir(parents=True)
    (root / "backend").mkdir()
    (root / "configuration/base.yaml").write_text("database: {password: hunter2}\n")
    (root / "configuration/environments/test.yaml").write_text("{}\n")
    with pytest.raises(ConfigurationError, match="sets secret database.password"):
        load_settings("test", root=root, env={})


def test_contracts_export_is_in_sync():
    import subprocess
    cp = subprocess.run([sys.executable, str(ROOT / "scripts/export_contracts.py"), "--check"], capture_output=True,
                        text=True)
    assert cp.returncode == 0, cp.stdout
