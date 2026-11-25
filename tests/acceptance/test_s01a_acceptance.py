"""Stage 01A acceptance tests S01A-T01 … S01A-T27 (PROTECTED). Real controller, real PostgreSQL, real isolated
processes, harmless fixture handlers. Proposals are explicit test data, never presented as model output."""
from __future__ import annotations

import os
import threading
import time
import uuid
from pathlib import Path

import psycopg
import pytest
import yaml

from s01a_common import ROOT, ExplicitTestProposals, P, TaskSpec, build, record, rules, start

from backend.capability_registry.numbering import NumberingError, load_numbers
from backend.contracts import canonical_hash
from backend.orchestration.authorization import AuthorizationError
from backend.orchestration.contracts import TaskContract
from backend.orchestration.controller import ControllerError, PlanningBlocked
from backend.policy_engine import SelfGrantError
from backend.contracts import Principal, PrincipalKind


@pytest.fixture()
def h(tmp_path):
    return build(tmp_path)


def runtime_conn(h):
    return psycopg.connect(h.settings.database.conninfo())


# ------------------------------------------------------------------------------------------------ T01-T02
def test_S01A_T01_create_and_persist_objective_contract(h):
    o = h.objective(approve=False)
    row = h.store.objective(o.objective_id)
    assert row[0] == o and row[1]["approval_status"] == "draft" and row[1]["version"] == 1
    assert o.original_source_sha256 == canonical_hash  or len(o.original_source_sha256) == 64
    import hashlib
    assert o.original_source_sha256 == hashlib.sha256((o.original_instructions + "\0").encode()).hexdigest()
    approved = h.controller.approve_objective(o.objective_id, h.auth("approve_objective", o.objective_id, o.hash()))
    assert approved.contract_version == 2 and approved.approval_status == "approved"
    assert approved.original_instructions == o.original_instructions
    with h.store.db.tx() as x:
        versions = x.execute("SELECT version, approval_status, approved_by, contract_hash FROM control.objectives "
                             "WHERE objective_id = %s ORDER BY version", (o.objective_id,)).fetchall()
    assert [v["version"] for v in versions] == [1, 2] and versions[1]["approved_by"] == "test-operator"
    for field in ("objective_id", "original_instructions", "original_source_sha256", "required_deliverables",
                  "functional_requirements", "non_functional_requirements", "restrictions", "required_capabilities",
                  "acceptance_criteria", "approved_decisions", "dependencies", "completion_conditions",
                  "contract_version", "created_at", "approval_status"):
        assert field in approved.model_dump()
    record("S01A-T01", {"versions": versions})


def test_S01A_T02_reject_unauthorised_objective_modification(h, tmp_path):
    o = h.objective()
    with pytest.raises(AuthorizationError):
        h.controller.amend_objective(o.objective_id, {"restrictions": ["none"]}, "drop restrictions", None)
    wrong = h.auth("amend_objective", o.objective_id, "0" * 64)
    with pytest.raises(AuthorizationError):
        h.controller.amend_objective(o.objective_id, {"restrictions": ["none"]}, "x", wrong)
    from backend.capability_registry.trust import generate_keypair
    generate_keypair(tmp_path / "rogue.pem", tmp_path / "rogue.pub")
    changes = {"restrictions": ["only report.txt"]}
    payload = canonical_hash({"base": o.hash(), "changes": changes})
    with pytest.raises(AuthorizationError, match="signature"):
        h.controller.amend_objective(o.objective_id, changes, "x",
                                     h.auth("amend_objective", o.objective_id, payload, key=tmp_path / "rogue.pem"))
    with pytest.raises(ControllerError, match="immutable"):
        h.controller.amend_objective(o.objective_id, {"original_instructions": "something else"}, "x", None)
    with runtime_conn(h) as c:
        for sql in ("UPDATE control.objectives SET original_instructions = 'hacked' WHERE objective_id = %s",
                    "DELETE FROM control.objectives WHERE objective_id = %s"):
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                c.execute(sql, (o.objective_id,))
            c.rollback()
    amended = h.controller.amend_objective(o.objective_id, changes, "operator narrowed scope",
                                           h.auth("amend_objective", o.objective_id, payload))
    assert amended.contract_version == o.contract_version + 1
    assert amended.original_instructions == o.original_instructions and amended.restrictions == ["only report.txt"]
    assert h.store.objective(o.objective_id, 1)[0].restrictions == ["only the task workspace"]
    record("S01A-T02", {"amended_version": amended.contract_version})


# ------------------------------------------------------------------------------------------------ T03-T05
def test_S01A_T03_permanent_numbered_identifiers(h):
    nums = load_numbers(ROOT / "configuration/tool_numbers.yaml")
    assert nums["040.001"].tool == "ops.system_status:report"
    tools = h.catalogue.all()
    assert tools and not h.catalogue.problems
    required = {"tool_id", "string_id", "capability_id", "name", "description", "operations", "input_schema",
                "output_schema", "required_permissions", "risk", "resource_types", "timeout_s", "resource_limits",
                "cancellable", "idempotency", "version", "available", "availability"}
    for t in tools:
        d = t.describe()
        assert required <= set(d), required - set(d)
        reg = h.registry.get(t.spec.capability_id)
        cap = int(reg.manifest.requirement.split("-")[1])
        assert t.number.startswith(f"{cap:03d}."), (t.number, reg.manifest.requirement)
    assert h.catalogue.get("014.001").spec.string_id == "test.text:write_text"
    # A number in the wrong capability range is refused.
    bad = build(h.root.parent, extra_numbers={"039.001": "test.text:count_words"}, numbers={
        k: v for k, v in __import__("s01a_harness").NUMBERS.items() if v != "test.text:count_words"})
    assert any("does not match its capability" in p for p in bad.catalogue.problems)
    record("S01A-T03", {"tools": [t.describe()["tool_id"] + " " + t.spec.string_id for t in tools]})


def test_S01A_T04_identifiers_preserved_after_restart(h):
    first = {t.number: t.spec.string_id for t in h.catalogue.all()}
    again = build(h.root.parent, reuse=h)
    assert {t.number: t.spec.string_id for t in again.catalogue.all()} == first
    with h.store.db.tx() as x:
        stored = {r["number"]: r["tool_string"] for r in x.execute(
            "SELECT number, tool_string FROM control.tool_identifiers WHERE namespace = %s", (h.namespace,)).fetchall()}
    assert stored == {k: v for k, v in yaml.safe_load(h.numbers_file.read_text())["tools"].items() and
                      {n: e["tool"] for n, e in yaml.safe_load(h.numbers_file.read_text())["tools"].items()}.items()}
    data = yaml.safe_load(h.numbers_file.read_text())
    data["tools"]["014.001"] = {"tool": "test.text:count_words"}          # reassignment attempt
    data["tools"].pop("014.005")
    h.numbers_file.write_text(yaml.safe_dump(data))
    re = build(h.root.parent, reuse=h)
    assert any("would be reassigned" in p for p in re.catalogue.problems)
    assert re.catalogue.all() == [], "an inconsistent numbering file makes no tool allocatable"
    data["tools"]["014.001"] = {"tool": "test.text:write_text"}
    data["tools"]["014.005"] = {"tool": "test.text:count_words", "retired": True}
    h.numbers_file.write_text(yaml.safe_dump(data))
    re = build(h.root.parent, reuse=h)
    assert not re.catalogue.problems and re.catalogue.get("014.005") is None
    data["tools"]["014.005"] = {"tool": "test.text:count_words"}
    h.numbers_file.write_text(yaml.safe_dump(data))
    assert any("retired" in p for p in build(h.root.parent, reuse=h).catalogue.problems)
    with h.store.db.tx() as x:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            x.execute("UPDATE control.tool_identifiers SET tool_string = 'x' WHERE namespace = %s AND number = '014.001'",
                      (h.namespace,))
    record("S01A-T04", {"numbers": first})


def test_S01A_T05_reject_duplicate_tool_identifiers(tmp_path):
    f = tmp_path / "n.yaml"
    f.write_text('tools:\n  "014.001": {tool: a:x}\n  "014.001": {tool: b:y}\n')
    with pytest.raises(NumberingError, match="duplicate identifier"):
        load_numbers(f)
    f.write_text('tools:\n  "014.001": {tool: a:x}\n  "014.002": {tool: a:x}\n')
    with pytest.raises(NumberingError, match="duplicate tool identifier"):
        load_numbers(f)
    h = build(tmp_path)
    m = h.caps / "test_text" / "capability.yaml"
    m.write_text(m.read_text().replace("name: count_words", "name: write_text"))
    states = {a.id: (a.state.value, a.problems) for a in h.registry.discover()}
    assert states["test.text"][0] == "invalid" and "duplicate tool name" in states["test.text"][1][0]
    record("S01A-T05", {"test.text": states["test.text"]})


# ------------------------------------------------------------------------------------------------ T06-T09
def test_S01A_T06_generate_and_validate_task_contract(h):
    o = h.objective()
    (c,) = h.controller.plan(o.objective_id, [h.write_task("TASK-0001", "FR-01", "a.txt", "alpha")])
    fields = ["task_id", "parent_objective_id", "parent_task_id", "contract_version", "contract_hash", "requirement_ids",
              "task_type", "task_description", "required_outcome", "permitted_inputs", "input_checksums",
              "required_outputs", "output_schemas", "permitted_tool_ids", "permitted_resource_ids",
              "permitted_filesystem_paths", "permitted_network_destinations", "permitted_operations",
              "prohibited_operations", "dependency_task_ids", "acceptance_criteria", "verification_handler_ids",
              "maximum_actions", "maximum_retries", "maximum_failed_steps", "maximum_parallel_tools",
              "execution_deadline", "resource_budget", "approval_requirements", "rollback_procedure",
              "assigned_worker", "task_status"]
    assert list(TaskContract.model_fields) == fields and len(fields) == 32 - 0  # 31 named + contract_hash listed
    assert c.contract_hash == c.compute_hash() and h.store.contract(o.objective_id, "TASK-0001") == c
    assert h.store.task(o.objective_id, "TASK-0001")["state"] == "READY"
    body = c.model_dump(mode="json")
    for broken in ({k: v for k, v in body.items() if k != "acceptance_criteria"}, {**body, "acceptance_criteria": []},
                   {**body, "surprise": 1}, {**body, "permitted_filesystem_paths": ["../x"]},
                   {**body, "dependency_task_ids": ["TASK-0001"]}):
        with pytest.raises(Exception):
            TaskContract.model_validate(broken)
    record("S01A-T06", {"contract": body})


def test_S01A_T07_reject_task_with_unknown_tools(h):
    o = h.objective()
    good = h.controller.build_contract(o, h.write_task("TASK-0001", "FR-01", "a.txt", "alpha"))
    bad = good.model_copy(update={"permitted_tool_ids": ["014.001", "039.999"], "contract_hash": ""}).sealed()
    with pytest.raises(ControllerError) as e:
        h.controller.submit_contract(o.objective_id, bad)
    assert any("unknown tool identifier 039.999" in p for p in e.value.details)
    forged = good.model_copy(update={"permitted_tool_ids": ["014.001", "014.002"]})   # hash not recomputed
    with pytest.raises(ControllerError) as e:
        h.controller.submit_contract(o.objective_id, forged)
    assert any("contract_hash" in p for p in e.value.details)
    with pytest.raises(Exception):
        TaskContract.model_validate({**good.model_dump(mode="json"), "permitted_tool_ids": ["write_text"]})
    assert h.store.tasks(o.objective_id) == []
    record("S01A-T07", {"rejected": e.value.details})


def test_S01A_T08_reject_circular_dependencies(h):
    o = h.objective()
    specs = [h.write_task("TASK-0001", "FR-01", "a.txt", "a", deps=["TASK-0003"]),
             h.write_task("TASK-0002", "FR-02", "b.txt", "b", deps=["TASK-0001"]),
             h.write_task("TASK-0003", "FR-03", "c.txt", "c", deps=["TASK-0002"])]
    with pytest.raises(ControllerError, match="circular") as e:
        h.controller.plan(o.objective_id, specs)
    assert h.store.tasks(o.objective_id) == []
    with pytest.raises(ControllerError, match="unknown dependency|contract rejected"):
        h.controller.plan(o.objective_id, [h.write_task("TASK-0001", "FR-01", "a.txt", "a", deps=["TASK-0099"])])
    record("S01A-T08", {"cycle": e.value.details})


def test_S01A_T09_allocate_only_compatible_tools(h):
    from backend.orchestration.allocation import ToolRequirement
    a = h.controller.allocator.allocate(ToolRequirement("document.text", ("validate",)))
    assert a.status == "ALLOCATED" and a.tool_ids == ["014.004"]
    assert a.rejected["040.902"] == "different task type" and a.rejected["040.904"].startswith("different")
    assert "014.001" not in a.candidates
    blocked = h.controller.allocator.allocate(ToolRequirement("document.text", ("translate",)))
    assert blocked.status == "BLOCKED" and "translate" in blocked.missing[0]
    invented = h.controller.allocator.allocate(ToolRequirement("document.text", ("validate",)), proposed=["014.004", "099.001"])
    assert invented.status == "BLOCKED" and "099.001" in invented.missing[0]
    risky = h.controller.allocator.allocate(ToolRequirement("risky.test", ("run",)))
    assert risky.status == "BLOCKED" and "risk medium" in risky.rejected["019.001"]
    dead = h.controller.allocator.allocate(ToolRequirement("test.unhealthy", ("execute",)))
    assert dead.status == "BLOCKED" and "unhealthy" in dead.rejected["040.904"]
    o = h.objective()
    with pytest.raises(PlanningBlocked) as e:
        h.controller.plan(o.objective_id, [TaskSpec(task_id="TASK-0001", requirement_ids=["FR-01"],
                          task_type="document.text", description="translate", required_outcome="translated",
                          operations=["translate"], acceptance_criteria=[{"id": "TC-01", "description": "x",
                          "requirement_ids": ["FR-01"], "verifier": "execution_succeeded"}])])
    assert e.value.details["status"] == "BLOCKED" and e.value.details["missing"]
    record("S01A-T09", {"allocated": a.tool_ids, "blocked": e.value.details})


# ------------------------------------------------------------------------------------------------ T10-T16
def test_S01A_T10_reject_tools_outside_pool(h):
    o = h.objective()
    c, ctx = start(h, o.objective_id, h.write_task("TASK-0001", "FR-01", "a.txt", "alpha"))
    r = h.controller.gateway.execute(ctx, P("TASK-0001", "014.005", "count", {"path": "a.txt"}))
    assert r.status == "rejected" and rules(r) == ["tool_not_in_pool"]
    r = h.controller.gateway.execute(ctx, P("TASK-0001", "039.123", "count", {"path": "a.txt"}))
    assert r.status == "rejected" and rules(r) == ["unknown_tool"]
    assert h.store.executions(o.objective_id) == []
    record("S01A-T10", {"violations": [v.__dict__ for v in r.violations]})


def test_S01A_T11_worker_cannot_increase_its_own_permissions(h):
    o = h.objective()
    c, ctx = start(h, o.objective_id, h.write_task("TASK-0001", "FR-01", "a.txt", "alpha"))
    r = h.controller.gateway.execute(ctx, P("TASK-0001", "014.001", "write", {"path": "a.txt", "text": "x"},
                                            targets=["pool:TASK-0001"]))
    assert "protected_controller_state" in rules(r)
    from dataclasses import replace
    forged = replace(ctx, contract_hash=canonical_hash({"pool": ["014.001", "014.005"]}))
    assert rules(h.controller.gateway.execute(forged, P("TASK-0001", "014.001", "write",
                                                        {"path": "a.txt", "text": "x"}))) == ["unauthenticated_context"]
    with pytest.raises(ControllerError, match="immutable during an execution attempt"):
        h.controller.change_tool_pool(o.objective_id, "TASK-0001", ["014.001", "014.005"], "more", None)
    worker = Principal(id="worker-1", kind=PrincipalKind.WORKER)
    with pytest.raises(SelfGrantError):
        h.controller.policy.grant(worker, worker, "workspace.write")
    fake = Principal(id="controller:fake", kind=PrincipalKind.SERVICE, permissions=frozenset({"workspace.write"}),
                     authenticated=True)
    from backend.policy_engine import PolicyRequest
    from backend.contracts import RiskClass
    d = h.controller.policy.evaluate(PolicyRequest(principal=fake, operation="tool.execute", risk=RiskClass.LOW,
                                                   required_permissions=frozenset({"workspace.write"}),
                                                   context={k: "x" for k in ("contract_hash", "tool_id", "arguments_hash", "execution_id")}))
    assert not d.allowed and "attested" in d.reason
    with runtime_conn(h) as conn:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute("UPDATE control.task_contracts SET contract = jsonb_set(contract, '{permitted_tool_ids}', "
                         "'[\"014.005\"]') WHERE objective_id = %s", (o.objective_id,))
    assert h.store.contract(o.objective_id, "TASK-0001").permitted_tool_ids == c.permitted_tool_ids
    record("S01A-T11", {"pool": c.permitted_tool_ids})


def test_S01A_T12_default_four_tool_limit(h, tmp_path):
    o = h.objective()
    five = h.write_task("TASK-0001", "FR-01", "a.txt", "a", operations=["write", "read", "edit", "validate", "count"])
    with pytest.raises(PlanningBlocked) as e:
        h.controller.plan(o.objective_id, [five])
    assert "limit is 4" in e.value.details["missing"][0]
    from backend.orchestration.config import load_controller_config
    cfg = load_controller_config()
    cfg = cfg.model_copy(update={"tool_limit_exceptions": {"document.text": 5}})
    h2 = build(tmp_path, config=cfg)
    o2 = h2.objective()
    (c,) = h2.controller.plan(o2.objective_id, [five])
    assert len(c.permitted_tool_ids) == 5
    record("S01A-T12", {"blocked": e.value.details["missing"], "exception_pool": c.permitted_tool_ids})


def test_S01A_T13_four_active_task_limit(h):
    o = h.objective(n_reqs=6)
    h.controller.plan(o.objective_id, [h.write_task(f"TASK-{i:04d}", f"FR-{i:02d}", f"f{i}.txt", f"p{i}")
                                       for i in range(1, 7)])
    first = h.controller.release(o.objective_id)
    assert len(first) == 4 and h.store.active_count(o.objective_id) == 4
    assert h.controller.release(o.objective_id) == []
    states = {t["task_id"]: t["state"] for t in h.store.tasks(o.objective_id)}
    assert sum(1 for s in states.values() if s == "QUEUED") == 4 and sum(1 for s in states.values() if s == "READY") == 2
    record("S01A-T13", {"released": first, "states": states})


def test_S01A_T14_concurrent_controllers_cannot_exceed_limit(h):
    o = h.objective(n_reqs=10)
    h.controller.plan(o.objective_id, [h.write_task(f"TASK-{i:04d}", f"FR-{i:02d}", f"f{i}.txt", f"p{i}")
                                       for i in range(1, 11)])
    controllers = [build(h.root.parent, reuse=h, controller_id=f"ctl-{n}").controller for n in range(5)]
    got, barrier = [], threading.Barrier(len(controllers))

    def go(ctl):
        barrier.wait()
        got.extend(ctl.release(o.objective_id))
    threads = [threading.Thread(target=go, args=(c,)) for c in controllers]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(got) == 4 and len(set(got)) == 4 and h.store.active_count(o.objective_id) == 4
    record("S01A-T14", {"granted": got})


def test_S01A_T15_no_execution_before_dependencies_complete(h):
    o = h.objective()
    h.controller.plan(o.objective_id, [h.write_task("TASK-0001", "FR-01", "a.txt", "a"),
                                       h.write_task("TASK-0002", "FR-02", "b.txt", "b", deps=["TASK-0001"])])
    assert h.store.task(o.objective_id, "TASK-0002")["state"] == "WAITING_FOR_DEPENDENCY"
    assert h.controller.release(o.objective_id) == ["TASK-0001"]
    from backend.orchestration.store import StaleState
    with pytest.raises(StaleState):
        h.controller.run_task(o.objective_id, "TASK-0002", ExplicitTestProposals([]))
    h.controller.run_task(o.objective_id, "TASK-0001", ExplicitTestProposals(h.write_proposals("TASK-0001", "FR-01", "a.txt", "a")))
    h.store.release_slot(o.objective_id, "TASK-0001")
    assert h.controller.release(o.objective_id) == ["TASK-0002"]
    record("S01A-T15", {"order": ["TASK-0001", "TASK-0002"]})


def test_S01A_T16_no_progression_after_failed_integration(h):
    o = h.objective(integration_checks=[{"id": "IC-01", "description": "a.txt says gamma", "after_tasks": ["TASK-0001"],
                                         "requirement_ids": ["FR-01"], "verifier": "text_contains",
                                         "params": {"path": "a.txt", "all": ["gamma"]}}])
    h.controller.plan(o.objective_id, [h.write_task("TASK-0001", "FR-01", "a.txt", "alpha"),
                                       h.write_task("TASK-0002", "FR-02", "b.txt", "beta", deps=["TASK-0001"])])
    srcs = {"TASK-0001": ExplicitTestProposals(h.write_proposals("TASK-0001", "FR-01", "a.txt", "alpha")),
            "TASK-0002": ExplicitTestProposals(h.write_proposals("TASK-0002", "FR-02", "b.txt", "beta"))}
    r = h.controller.run_objective(o.objective_id, srcs)
    assert r["status"] == "blocked" and r["batches"][0]["integration"][0]["passed"] is False
    assert h.store.task(o.objective_id, "TASK-0002")["state"] in ("WAITING_FOR_DEPENDENCY", "READY")
    assert h.controller.release(o.objective_id) == []
    assert h.store.latest_checkpoint(o.objective_id, verified_only=True) is None
    record("S01A-T16", {"result": r["status"], "integration": r["batches"][0]["integration"]})


# ------------------------------------------------------------------------------------------------ T17-T22
def test_S01A_T17_reject_malformed_proposals(h, tmp_path):
    o = h.objective()
    h.controller.plan(o.objective_id, [h.write_task("TASK-0001", "FR-01", "a.txt", "a")])
    h.controller.release(o.objective_id)
    marker = tmp_path / "shell_marker"
    src = ExplicitTestProposals([f"touch {marker}", '{"proposal_id": "p-1234", "task_id": "TASK-0001"}',
                                 {"proposal_id": "p-2222", "task_id": "TASK-0001", "contract_version": 1,
                                  "tool_id": "014.001", "operation": "write", "arguments": {}, "target_resources": [],
                                  "expected_result": "x", "referenced_requirement_ids": ["FR-01"], "run_shell": "yes"},
                                 "never requested: the task is blocked first"])
    state = h.controller.run_task(o.objective_id, "TASK-0001", src)
    assert state == "BLOCKED" and not marker.exists()
    assert len(src.seen_packets) == 3, "bounded corrections: 2 corrections after the first failure, then BLOCKED"
    fb = src.seen_feedback[1]
    assert fb[0]["field"] == "<root>" and fb[0]["permitted"]
    assert any(e["field"] in ("tool_id", "contract_version") for e in src.seen_feedback[2])
    with h.store.db.tx() as x:
        rows = x.execute("SELECT accepted, errors FROM control.proposals WHERE objective_id = %s ORDER BY at",
                         (o.objective_id,)).fetchall()
    assert [r["accepted"] for r in rows] == [False, False, False]
    assert any(e["field"] == "run_shell" for e in rows[2]["errors"])
    assert h.store.executions(o.objective_id) == []
    record("S01A-T17", {"feedback": src.seen_feedback[1:]})


def test_S01A_T18_reject_invalid_tool_arguments(h):
    o = h.objective()
    c, ctx = start(h, o.objective_id, h.write_task("TASK-0001", "FR-01", "a.txt", "a"))
    for args in ({"path": "a.txt"}, {"path": "a.txt", "text": 5}, {"path": "a.txt", "text": "x", "mode": "rm"}):
        r = h.controller.gateway.execute(ctx, P("TASK-0001", "014.001", "write", args, targets=["file:a.txt"]))
        assert r.status == "rejected" and "invalid_arguments" in rules(r), args
    assert h.store.executions(o.objective_id) == []


def test_S01A_T19_reject_unauthorised_resource_ids(h):
    o = h.objective()
    c, ctx = start(h, o.objective_id, h.write_task("TASK-0001", "FR-01", "a.txt", "a"))
    r = h.controller.gateway.execute(ctx, P("TASK-0001", "014.001", "write", {"path": "a.txt", "text": "x"},
                                            targets=["file:other.txt"]))
    assert rules(r) == ["resource_not_permitted"] and r.violations[0].permitted == ("file:a.txt",)


def test_S01A_T20_reject_unauthorised_filesystem_paths(h, tmp_path):
    o = h.objective()
    c, ctx = start(h, o.objective_id, h.write_task("TASK-0001", "FR-01", "a.txt", "a"))
    ws = h.controller.gateway.workspace(o.objective_id)
    outside = tmp_path / "outside"
    outside.mkdir()
    (ws / "link").symlink_to(outside)
    for p in ("../escape.txt", "/etc/passwd", "sub/../../x.txt", "link/x.txt", "b.txt", "~/x", "C:/x"):
        r = h.controller.gateway.execute(ctx, P("TASK-0001", "014.001", "write", {"path": p, "text": "x"},
                                                targets=["file:a.txt"]))
        assert r.status == "rejected" and "path_not_permitted" in rules(r), p
    assert not list(outside.iterdir()) and not (tmp_path / "escape.txt").exists()
    ok = h.controller.gateway.execute(ctx, P("TASK-0001", "014.001", "write", {"path": "a.txt", "text": "x"},
                                             targets=["file:a.txt"]))
    assert ok.status == "executed", (ok.reason, rules(ok))


def test_S01A_T21_reject_unauthorised_network_destinations(h):
    o = h.objective()
    spec = TaskSpec(task_id="TASK-0001", requirement_ids=["FR-01"], task_type="ops.test", description="record url",
                    required_outcome="recorded", operations=["fetch"], permitted_network_destinations=["example.org"],
                    acceptance_criteria=[{"id": "TC-01", "description": "ran", "requirement_ids": ["FR-01"],
                                          "verifier": "execution_succeeded"}])
    c, ctx = start(h, o.objective_id, spec)
    for url in ("https://evil.example.com/x", "ftp://example.org/x", "http://169.254.169.254/latest", "file:///etc/passwd"):
        r = h.controller.gateway.execute(ctx, P("TASK-0001", "040.906", "fetch", {"url": url}))
        assert r.status == "rejected" and "network_not_permitted" in rules(r), url
    assert h.controller.gateway.execute(ctx, P("TASK-0001", "040.906", "fetch", {"url": "https://example.org/a"})).status == "executed"


def test_S01A_T22_reject_attempts_to_change_acceptance_criteria(h):
    o = h.objective()
    c, ctx = start(h, o.objective_id, h.write_task("TASK-0001", "FR-01", "a.txt", "alpha"))
    for target in ("criteria:TASK-0001", "verifier:text_contains", "budget:TASK-0001", "objective:" + o.objective_id):
        r = h.controller.gateway.execute(ctx, P("TASK-0001", "014.001", "write", {"path": "a.txt", "text": "x"},
                                                targets=[target]))
        assert "protected_controller_state" in rules(r), target
    from backend.orchestration.proposals import parse_proposal
    bad = P("TASK-0001", "014.001", "write", {"path": "a.txt", "text": "x"}).model_dump() | {
        "acceptance_criteria": [], "maximum_actions": 999}
    assert parse_proposal(bad, c, {}).proposal is None
    with runtime_conn(h) as conn:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute("DELETE FROM control.task_contracts WHERE objective_id = %s", (o.objective_id,))
    with pytest.raises(AuthorizationError):
        h.controller.amend_objective(o.objective_id, {"acceptance_criteria": [
            {"id": "AC-01", "description": "nothing", "requirement_ids": ["FR-01"]}]}, "weaken", None)
    assert h.store.contract(o.objective_id, "TASK-0001").acceptance_criteria == c.acceptance_criteria


# ------------------------------------------------------------------------------------------------ T23-T27
def test_S01A_T23_detect_repeated_unsuccessful_patterns(h):
    o = h.objective()
    spec = h.write_task("TASK-0001", "FR-01", "a.txt", "a", operations=["write", "edit", "fail"], maximum_retries=1)
    c, ctx = start(h, o.objective_id, spec)
    out = [h.controller.gateway.execute(ctx, P("TASK-0001", "014.006", "fail", {"n": 1})) for _ in range(3)]
    assert [r.status for r in out] == ["failed", "failed", "rejected"] and "repeated_failure" in rules(out[2])
    o2 = h.objective()
    spec2 = TaskSpec(task_id="TASK-0001", requirement_ids=["FR-01"], task_type="ops.test", description="noop",
                     required_outcome="ran ok", operations=["noop"], acceptance_criteria=[
                         {"id": "TC-01", "description": "ran", "requirement_ids": ["FR-01"], "verifier": "execution_succeeded"}])
    c2, ctx2 = start(h, o2.objective_id, spec2)
    seq = [h.controller.gateway.execute(ctx2, P("TASK-0001", "040.902", "noop", {"n": n})) for n in (1, 2, 1, 2)]
    assert [r.status for r in seq[:3]] == ["executed"] * 3 and "circular_pattern" in rules(seq[3])
    o3 = h.objective()
    c3, ctx3 = start(h, o3.objective_id, spec2)
    same = [h.controller.gateway.execute(ctx3, P("TASK-0001", "040.902", "noop", {"n": 7})) for _ in range(4)]
    assert [r.status for r in same[:3]] == ["executed"] * 3 and "no_progress" in rules(same[3])
    record("S01A-T23", {"repeated": rules(out[2]), "circular": rules(seq[3]), "no_progress": rules(same[3])})


def test_S01A_T24_enforce_maximum_action_budget(h):
    o = h.objective()
    spec = TaskSpec(task_id="TASK-0001", requirement_ids=["FR-01"], task_type="ops.test", description="noop",
                    required_outcome="ran ok", operations=["noop"], maximum_actions=3, acceptance_criteria=[
                        {"id": "TC-01", "description": "ran", "requirement_ids": ["FR-01"], "verifier": "execution_succeeded"}])
    with pytest.raises(ControllerError, match="exceeds the trusted limit"):
        h.controller.plan(o.objective_id, [TaskSpec(**{**spec.__dict__, "maximum_actions": 31})])
    c, ctx = start(h, o.objective_id, spec)
    res = [h.controller.gateway.execute(ctx, P("TASK-0001", "040.902", "noop", {"n": n})) for n in range(4)]
    assert [r.status for r in res] == ["executed"] * 3 + ["rejected"] and "action_budget_exhausted" in rules(res[3])
    assert h.store.task(o.objective_id, "TASK-0001")["state"] == "BLOCKED"
    assert h.store.budgets(o.objective_id, "TASK-0001")["actions"]["consumed"] == 3


def test_S01A_T25_enforce_retry_limits(h):
    o = h.objective()
    c, ctx = start(h, o.objective_id, h.write_task("TASK-0001", "FR-01", "a.txt", "a", operations=["write", "edit", "fail"],
                                                   maximum_retries=2, maximum_failed_steps=3))
    res = [h.controller.gateway.execute(ctx, P("TASK-0001", "014.006", "fail", {"n": 5})) for _ in range(3)]
    assert [r.status for r in res] == ["failed", "failed", "failed"]
    assert h.store.task(o.objective_id, "TASK-0001")["state"] == "FAILED"   # third failure also hits failed-step limit
    o2 = h.objective()
    c2, ctx2 = start(h, o2.objective_id, h.write_task("TASK-0001", "FR-01", "a.txt", "a", operations=["write", "edit", "fail"],
                                                      maximum_retries=1))
    r = [h.controller.gateway.execute(ctx2, P("TASK-0001", "014.006", "fail", {"n": 5})) for _ in range(3)]
    assert [x.status for x in r] == ["failed", "failed", "rejected"] and "repeated_failure" in rules(r[2])
    assert len(h.store.executions(o2.objective_id)) == 2


def test_S01A_T26_enforce_failed_step_limit(h):
    o = h.objective()
    c, ctx = start(h, o.objective_id, h.write_task("TASK-0001", "FR-01", "a.txt", "a", operations=["write", "edit", "fail"],
                                                   maximum_failed_steps=2))
    r1 = h.controller.gateway.execute(ctx, P("TASK-0001", "014.006", "fail", {"n": 1}))
    r2 = h.controller.gateway.execute(ctx, P("TASK-0001", "014.006", "fail", {"n": 2}))
    assert r1.task_state == "RUNNING" and r2.task_state == "FAILED"
    r3 = h.controller.gateway.execute(ctx, P("TASK-0001", "014.001", "write", {"path": "a.txt", "text": "x"},
                                             targets=["file:a.txt"]))
    assert r3.status == "rejected" and rules(r3) == ["invalid_state"]


def test_S01A_T27_enforce_concurrent_tool_limit(h, tmp_path):
    o = h.objective()
    spec = TaskSpec(task_id="TASK-0001", requirement_ids=["FR-01"], task_type="ops.test", description="sleep",
                    required_outcome="ran ok", operations=["run"], maximum_parallel_tools=1, acceptance_criteria=[
                        {"id": "TC-01", "description": "ran", "requirement_ids": ["FR-01"], "verifier": "execution_succeeded"}])
    c, ctx = start(h, o.objective_id, spec)
    results, go = [], threading.Barrier(2)

    def run(i):
        go.wait()
        results.append(h.controller.gateway.execute(ctx, P("TASK-0001", "040.901", "run",
                                                         {"marker": f"m{i}.txt", "delay": 30, "sleep": 1.2})))
    ts = [threading.Thread(target=run, args=(i,)) for i in range(2)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    statuses = sorted(r.status for r in results)
    assert statuses == ["executed", "rejected"]
    assert "concurrency_limit" in rules(next(r for r in results if r.status == "rejected"))
    assert h.store.budgets(o.objective_id, "TASK-0001")["concurrent_tools"]["reserved"] == 0
    record("S01A-T27", {"statuses": statuses})
