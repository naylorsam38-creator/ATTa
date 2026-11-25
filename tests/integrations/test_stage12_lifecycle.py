"""Stage 12 — install transaction, rollback, registration, stable ids, enable/disable, trust, upgrade, removal,
restart persistence, version pinning and self-grant refusals."""
from __future__ import annotations

import shutil
import uuid

import psycopg
import pytest

from stage12_harness import P, TrustClass, build12, canonical_hash, record, start, write_notes

from backend.capability_registry.trust import generate_keypair
from backend.contracts import AvailabilityState, Principal, PrincipalKind
from backend.integrations.manager import IntegrationError
from backend.integrations.types import AUTO_NUMBER_BAND
from backend.orchestration.authorization import AuthorizationError, sign_authorization
from backend.policy_engine.engine import SelfGrantError

pytestmark = pytest.mark.requires_services
CONN = "test.notes_connector"
PLUG = "test.text_plugin"


def test_S12_T10_install_transaction_registers_in_the_existing_registry_and_catalogue(tmp_path):
    H = build12(tmp_path, fixtures=["valid_connector"])
    reg_before = H.h.registry
    res = H.install("valid_connector")
    assert res["status"] == "INSTALLED", res
    states = [e["to_state"] for e in H.store.installation_events(CONN)]
    assert states[-5:] == ["INSTALLING", "REGISTERING", "VERIFYING", "INSTALLED"][-4:] or \
        states[-4:] == ["INSTALLING", "REGISTERING", "VERIFYING", "INSTALLED"], states
    assert H.mgr.registry is reg_before and H.h.controller.registry is reg_before, "no second registry"
    cap = H.h.registry.get("integration.test.notes_connector")
    assert cap is not None and str(H.mgr.packages_dir) in cap.source
    assert cap.state is AvailabilityState.DISABLED          # installed, not yet enabled
    for tool, number in res["tool_numbers"].items():
        t = H.h.catalogue.get(number)
        assert t is not None and t.spec.string_id == f"integration.{CONN}:{tool}"
        assert number.startswith("021.") and AUTO_NUMBER_BAND[0] <= int(number[4:]) <= AUTO_NUMBER_BAND[1]
        assert not t.available
    H.enable(CONN)
    assert H.h.registry.get("integration.test.notes_connector").state is AvailabilityState.AVAILABLE
    assert all(H.h.catalogue.get(n).available for n in res["tool_numbers"].values())
    with H.h.store.db.tx() as x:
        ids = {r["number"]: r["tool_string"] for r in x.execute(
            "SELECT number, tool_string FROM control.tool_identifiers WHERE namespace=%s", (H.h.namespace,)).fetchall()}
    assert all(ids[n] == f"integration.{CONN}:{t}" for t, n in res["tool_numbers"].items())
    record("S12-T10", {"result": res, "transitions": states, "registry_source": cap.source})


def test_S12_T11_failed_install_rolls_back_completely(tmp_path):
    H = build12(tmp_path, fixtures=["valid_connector", "invalid_skill"])
    out = {}
    for step in ("installing", "registering", "verifying"):
        def boom(s, iid, step=step):
            if s == step:
                raise RuntimeError(f"injected failure at {step}")
        H.mgr.fault_hook = boom
        v = H.mgr.validate(H.path("valid_connector"))
        res = H.mgr.install(H.path("valid_connector"), H.h.auth(v.approval_action, v.approval_subject,
                                                                v.approval_binding))
        assert res["status"] == "FAILED" and res["rolled_back"], res
        assert H.h.registry.get("integration.test.notes_connector") is None
        assert not (H.mgr.packages_dir / "integration.test.notes_connector").exists()
        assert list(H.mgr.staging_dir.iterdir()) == []
        assert all(t.spec.capability_id != "integration.test.notes_connector" for t in H.h.catalogue.all())
        assert H.mgr.get_installed(CONN) is None
        assert all(r["status"] != "registered" for r in H.store.tools(CONN))
        assert H.store.integration(CONN)["state"] == "FAILED"
        out[step] = res["problems"]
    H.mgr.fault_hook = None
    res = H.install("valid_connector")                      # a clean retry succeeds with the SAME numbers
    assert res["status"] == "INSTALLED"
    numbers_rows = {r["tool_string"]: r["number"] for r in H.store.numbers()}
    assert res["tool_numbers"]["search"] == numbers_rows["integration.test.notes_connector:search"]
    bad = H.install("invalid_skill")                         # verification failure of a skill (unresolved reference)
    assert bad["status"] == "FAILED" and any("does not resolve" in p for p in bad["problems"])
    assert H.store.skill("find_notes") is None
    record("S12-T11", {"failures": out, "retry": res["tool_numbers"], "invalid_skill": bad})


def test_S12_T12_stable_tool_ids_across_remove_reinstall_and_upgrade(tmp_path):
    H = build12(tmp_path, fixtures=["valid_connector", "valid_plugin", "valid_plugin_v1_1"])
    a = H.install("valid_connector")["tool_numbers"]
    p1 = H.install("valid_plugin", enable=True)["tool_numbers"]
    assert H.mgr.remove(CONN, "operator")["status"] == "REMOVED"
    assert H.store.integration(CONN)["state"] == "REMOVED"
    b = H.install("valid_connector")["tool_numbers"]
    assert a == b, "reinstalling the same tool must keep its permanent number"
    up = H.upgrade("valid_plugin_v1_1")
    assert up["status"] == "INSTALLED" and up["tool_numbers"] == p1
    assert set(a.values()).isdisjoint(p1.values())
    with H.h.store.db.tx() as x:   # the runtime role cannot re-point or delete a Stage 12 number
        with pytest.raises(psycopg.Error):
            x.execute("UPDATE integrations.tool_numbers SET tool_string='x' WHERE namespace=%s", (H.h.namespace,))
    with H.h.store.db.tx() as x:
        with pytest.raises(psycopg.Error):
            x.execute("DELETE FROM control.tool_identifiers WHERE namespace=%s", (H.h.namespace,))
    record("S12-T12", {"connector": a, "reinstalled": b, "plugin_v1": p1, "plugin_v1_1": up["tool_numbers"]})


def test_S12_T13_enable_disable_and_no_self_enable(tmp_path):
    H = build12(tmp_path, fixtures=["valid_plugin"])
    H.install("valid_plugin")
    n = H.number(PLUG, "summarize")
    with pytest.raises(IntegrationError):            # no authorisation at all
        H.mgr.enable(PLUG, None)
    rogue = tmp_path / "rogue.pem"
    generate_keypair(rogue, tmp_path / "rogue.pub")
    with pytest.raises(IntegrationError):            # an integration's own key is not the operator
        H.mgr.enable(PLUG, H.enable_auth(PLUG, key=rogue))
    H.enable(PLUG)
    assert H.h.catalogue.get(n).available
    H.mgr.disable(PLUG, "admin", "incident")
    assert H.store.integration(PLUG)["disabled_by_admin"] is True
    assert not H.h.registry.get("integration.test.text_plugin").executable
    H.h.catalogue.refresh_availability()
    assert not H.h.catalogue.get(n).available
    # an integration cannot re-enable itself: the only path needs a fresh operator signature
    with pytest.raises(IntegrationError):
        H.mgr.enable(PLUG, sign_authorization(rogue, "integration.enable", PLUG, "0" * 64, "plugin"))
    H.enable(PLUG)
    assert H.h.registry.get("integration.test.text_plugin").executable
    record("S12-T13", {"events": [e["event_type"] for e in H.store.events(PLUG)]})


def test_S12_T14_trust_classification(tmp_path):
    class FakeSandbox:   # TEST DOUBLE for the Stage 01B isolation interface (clearly not a real sandbox)
        def report(self):
            from backend.integrations.isolation import IsolationReport
            return IsolationReport(True, "test-double", ("declared by test",), "test double")
    H = build12(tmp_path, fixtures=["valid_plugin", "valid_connector"])
    res_u = H.install("valid_plugin", trust=TrustClass.UNTRUSTED, enable=True)
    assert res_u["status"] == "INSTALLED"
    cap = H.h.registry.get("integration.test.text_plugin")
    assert cap.state is AvailabilityState.UNTRUSTED and not cap.executable
    assert H.svc.health(PLUG)["status"] == "BLOCKED"
    H.mgr.set_trust(PLUG, TrustClass.RESTRICTED, H.h.auth("integration.trust", PLUG, canonical_hash(
        {"integration_id": PLUG, "version": "1.0.0", "trust_class": "RESTRICTED"})))
    h = H.svc.health(PLUG)
    assert h["status"] == "BLOCKED" and any("OS isolation required" in r for r in h["reasons"]), h
    H.mgr.isolation = FakeSandbox()
    assert H.svc.health(PLUG)["status"] == "HEALTHY"
    with pytest.raises(IntegrationError):      # trust can only change with an operator signature
        H.mgr.set_trust(PLUG, TrustClass.CORE, None)
    res_c = H.install("valid_connector", trust=TrustClass.RESTRICTED, enable=True)
    hc = H.svc.health(CONN)
    assert hc["status"] == "BLOCKED" and any("READ_ONLY" in r for r in hc["reasons"])
    perms = H.svc.permissions(CONN)
    record("S12-T14", {"untrusted": res_u["verification"], "restricted_plugin_with_sandbox": "HEALTHY",
                       "restricted_connector": hc, "permissions_view": perms})


def test_S12_T15_upgrade_detects_permission_expansion_and_requires_a_new_signature(tmp_path):
    H = build12(tmp_path, fixtures=["valid_plugin", "escalating_plugin", "valid_plugin_v1_1"])
    H.install("valid_plugin", enable=True)
    v_old = H.mgr.validate(H.path("valid_plugin_v1_1"))
    v = H.mgr.validate(H.path("escalating_plugin"))
    rep = v.upgrade_report
    assert rep["expands_authority"] and rep["permissions_added"] == ["external.simulated"]
    assert rep["tools_added"] == ["exfiltrate"] and rep["risk_increases"] == {"summarize": "low -> medium"}
    with pytest.raises(IntegrationError):    # a signature for a different (non-expanding) upgrade does not cover this
        H.mgr.upgrade(H.path("escalating_plugin"),
                      H.h.auth(v_old.approval_action, v_old.approval_subject, v_old.approval_binding))
    with pytest.raises(IntegrationError):    # nor does a signature over the right subject but the wrong payload
        H.mgr.upgrade(H.path("escalating_plugin"), H.h.auth(v.approval_action, v.approval_subject, "0" * 64))
    assert H.mgr.get_installed(PLUG).version == "1.0.0"
    res = H.mgr.upgrade(H.path("escalating_plugin"), H.h.auth(v.approval_action, v.approval_subject,
                                                              v.approval_binding))
    assert res["status"] == "INSTALLED" and res["state"] == "ENABLED"
    assert H.mgr.get_installed(PLUG).version == "1.2.0"
    up_events = [e for e in H.store.events(PLUG) if e["event_type"] == "upgraded"]
    assert up_events and up_events[-1]["details"]["upgrade_report"]["expands_authority"] is True
    record("S12-T15", {"report": rep, "result_numbers": res["tool_numbers"]})


def test_S12_T16_removal_handles_active_tasks_and_keeps_history(tmp_path):
    H = build12(tmp_path, fixtures=["valid_connector"])
    H.install("valid_connector", enable=True)
    n = H.number(CONN, "search")
    o = H.objective()
    c, ctx = start(H.h, o.objective_id, H.spec("TASK-0001", "connector.notes", ["search"], n))
    write_notes(H.workspace(o.objective_id))
    r = H.h.controller.gateway.execute(ctx, P("TASK-0001", n, "search", {"path": "notes.json", "query": "budget"}))
    assert r.status == "executed", r
    res = H.mgr.remove(CONN, "operator")
    assert res["status"] == "PENDING_ACTIVE_TASKS" and res["active_tasks"][0]["task_id"] == "TASK-0001"
    r2 = H.h.controller.gateway.execute(ctx, P("TASK-0001", n, "search", {"path": "notes.json", "query": "holiday"}))
    assert r2.status == "rejected" and r2.violations[0].rule == "capability_unavailable"
    H.h.controller.cancel_task(o.objective_id, "TASK-0001", "test ends task")
    res = H.mgr.remove(CONN, "operator")
    assert res["status"] == "REMOVED"
    assert H.h.registry.get("integration.test.notes_connector") is None and H.h.catalogue.get(n) is None
    assert not (H.mgr.packages_dir / "integration.test.notes_connector").exists()
    execs = H.h.store.executions(o.objective_id, "TASK-0001")
    assert any(e["state"] == "succeeded" and e["tool_number"] == n for e in execs), "old task records intact"
    assert H.store.pins(CONN) and H.store.manifest(H.store.integration(CONN)["approved_manifest_digest"]) is not None
    assert {r["status"] for r in H.store.tools(CONN)} == {"removed"}
    assert "removed" in [e["event_type"] for e in H.store.events(CONN)]
    record("S12-T16", {"pending": True, "events": [e["event_type"] for e in H.store.events(CONN)]})


def test_S12_T17_restart_persistence(tmp_path):
    H = build12(tmp_path, fixtures=["valid_connector", "valid_plugin"])
    a = H.install("valid_connector", enable=True)
    H.install("valid_plugin")
    H2 = build12(tmp_path, reuse=H)          # a new controller + registry + catalogue over the same DB and files
    rc, rp = H2.mgr.get_installed(CONN), H2.mgr.get_installed(PLUG)
    assert rc.state.value == "ENABLED" and rp.state.value == "INSTALLED"
    assert rc.numbers == a["tool_numbers"]
    assert H2.h.registry.get("integration.test.notes_connector").executable
    assert not H2.h.registry.get("integration.test.text_plugin").executable
    o = H2.objective()
    n = a["tool_numbers"]["search"]
    c, ctx = start(H2.h, o.objective_id, H2.spec("TASK-0001", "connector.notes", ["search"], n))
    write_notes(H2.workspace(o.objective_id))
    r = H2.h.controller.gateway.execute(ctx, P("TASK-0001", n, "search", {"path": "notes.json", "query": "budget"}))
    assert r.status == "executed" and r.output["count"] == 1
    # tampering with an installed package while stopped is detected at the next start
    (H2.mgr.packages_dir / "integration.test.text_plugin" / "plugin.py").write_text("# tampered\n")
    H3 = build12(tmp_path, reuse=H)
    assert H3.mgr.get_installed(PLUG).state.value == "BLOCKED"
    record("S12-T17", {"restored": {CONN: rc.state.value, PLUG: rp.state.value}, "numbers": rc.numbers})


def test_S12_T18_active_task_version_pinning(tmp_path):
    H = build12(tmp_path, fixtures=["valid_plugin", "valid_plugin_v1_1"])
    H.install("valid_plugin", enable=True)
    n = H.number(PLUG, "summarize")
    o = H.objective()
    c, ctx = start(H.h, o.objective_id, H.spec("TASK-0001", "plugin.text", ["read"], n, paths=["."]))
    r = H.h.controller.gateway.execute(ctx, P("TASK-0001", n, "read", {"text": "one two three"}))
    assert r.status == "executed" and r.output["version"] == "1.0.0"
    pin = H.store.pins(PLUG)[0]
    assert (pin["task_id"], pin["version"]) == ("TASK-0001", "1.0.0")
    deferred = H.upgrade("valid_plugin_v1_1")
    assert deferred["status"] == "DEFERRED" and deferred["active_tasks"][0]["task_id"] == "TASK-0001"
    r = H.h.controller.gateway.execute(ctx, P("TASK-0001", n, "read", {"text": "still the old version"}))
    assert r.output["version"] == "1.0.0"
    H.h.controller.cancel_task(o.objective_id, "TASK-0001", "operator stops the task")
    up = H.upgrade("valid_plugin_v1_1")
    assert up["status"] == "INSTALLED" and up["tool_numbers"]["summarize"] == n
    # an operator-authorised NEW attempt of the old task is still pinned to 1.0.0: it is refused, never swapped
    cc = H.h.store.contract(o.objective_id, "TASK-0001")
    H.h.controller.reattempt(o.objective_id, "TASK-0001", H.h.auth("reattempt_task", f"{o.objective_id}/TASK-0001",
                                                                   cc.contract_hash))
    t = H.h.store.transition(o.objective_id, "TASK-0001", "RUNNING", "test", H.h.controller.controller_id,
                             expect=["QUEUED"])
    ctx2 = H.h.controller.gateway.issue_context(o.objective_id, "TASK-0001", t["attempt_id"], cc.contract_hash,
                                                cc.assigned_worker)
    r = H.h.controller.gateway.execute(ctx2, P("TASK-0001", n, "read", {"text": "after upgrade"}))
    assert r.status == "rejected" and r.violations[0].rule == "integration_version_mismatch", r
    o2 = H.objective()                       # a NEW task gets the new version and records its own pin
    c2, ctx3 = start(H.h, o2.objective_id, H.spec("TASK-0002", "plugin.text", ["read"], n, paths=["."]))
    r = H.h.controller.gateway.execute(ctx3, P("TASK-0002", n, "read", {"text": "new task"}))
    assert r.status == "executed" and r.output["version"] == "1.1.0"
    record("S12-T18", {"pins": H.store.pins(PLUG), "deferred": deferred["status"]})


def test_S12_T19_no_self_grant_no_self_add_no_risk_or_budget_self_change(tmp_path):
    H = build12(tmp_path, fixtures=["valid_plugin", "valid_connector"])
    H.install("valid_plugin", enable=True)
    H.install("valid_connector", enable=True)
    pol = H.h.controller.policy
    plugin_principal = Principal(id="capability:integration.test.text_plugin", kind=PrincipalKind.CAPABILITY,
                                 permissions=frozenset(), authenticated=False)
    with pytest.raises(SelfGrantError):
        pol.grant(plugin_principal, plugin_principal, "external.simulated")
    n = H.number(PLUG, "summarize")
    o = H.objective()
    spec = H.spec("TASK-0001", "plugin.text", ["read"], n, paths=["."])
    H.h.controller.plan(o.objective_id, [spec])
    c = H.h.store.contract(o.objective_id, "TASK-0001")
    rogue = tmp_path / "rogue.pem"
    generate_keypair(rogue, tmp_path / "rogue.pub")
    other = H.number(CONN, "send")
    with pytest.raises(AuthorizationError):     # an integration cannot add itself (or another tool) to a task
        H.h.controller.change_tool_pool(o.objective_id, "TASK-0001", [n, other], "plugin asks",
                                        sign_authorization(rogue, "change_tool_pool", f"{o.objective_id}/TASK-0001",
                                                           "x" * 64, "plugin"))
    assert H.h.store.contract(o.objective_id, "TASK-0001").permitted_tool_ids == c.permitted_tool_ids
    # risk-class self-change: the installed package edits its own generated manifest -> integrity failure
    capdir = H.mgr.packages_dir / "integration.test.text_plugin"
    y = (capdir / "capability.yaml").read_text()
    (capdir / "capability.yaml").write_text(y.replace("risk: low", "risk: critical", 1))
    from backend.capability_registry import CapabilityNotExecutable
    with pytest.raises(CapabilityNotExecutable):
        H.h.registry.revalidate("integration.test.text_plugin")
    assert H.h.registry.get("integration.test.text_plugin").state is AvailabilityState.INTEGRITY_FAILED
    assert H.svc.health(PLUG)["status"] == "BLOCKED"
    (capdir / "capability.yaml").write_text(y)
    # budget self-change: limits come only from trusted controller configuration
    from backend.orchestration.controller import ControllerError
    with pytest.raises(ControllerError):
        H.h.controller.plan(o.objective_id, [H.spec("TASK-0002", "plugin.text", ["read"], n, paths=["."],
                                                    maximum_actions=10 ** 6)])
    record("S12-T19", {"self_grant": "refused", "pool_unchanged": c.permitted_tool_ids,
                       "risk_change": "integrity_failed", "budget_override": "refused"})


def test_S12_T20_capability_id_collision_and_dependency_checks(tmp_path):
    H = build12(tmp_path, fixtures=["valid_plugin"])
    # a non-Stage-12 package already uses the capability id the integration would get
    rogue = H.h.caps / "collide"
    shutil.copytree(H.h.caps / "test_ops", rogue)
    y = (rogue / "capability.yaml").read_text().replace("id: test.ops", "id: integration.test.text_plugin")
    (rogue / "capability.yaml").write_text(y)
    H.h.registry.discover()
    res = H.install("valid_plugin")
    assert res["status"] == "FAILED" and "collision" in res["problems"][0]
    record("S12-T20", res)


def _unused():
    return uuid
