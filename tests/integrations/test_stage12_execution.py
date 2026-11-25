"""Stage 12 — task allocation, gateway enforcement, result handling, prompt injection, offline mode, credentials,
health and audit. Every execution goes through the Stage 01A controller/gateway; proposals are explicit test data."""
from __future__ import annotations

import json
import socket

import psycopg
import pytest

from stage12_harness import ExplicitTestProposals, P, build12, record, start, write_notes

from backend.integrations.manager import IntegrationError
from backend.integrations.types import ConnectivityMode
from backend.integrations.network import NetworkPolicy

pytestmark = pytest.mark.requires_services
CONN, PLUG = "test.notes_connector", "test.text_plugin"


def test_S12_T30_end_to_end_connector_task_through_the_controller(tmp_path):
    H = build12(tmp_path, fixtures=["valid_connector"])
    H.install("valid_connector", enable=True)
    n = H.number(CONN, "search")
    o = H.objective()
    H.h.controller.plan(o.objective_id, [H.spec("TASK-0001", "connector.notes", ["search"], n)])
    c = H.h.store.contract(o.objective_id, "TASK-0001")
    assert c.permitted_tool_ids == [n], "the allocator chose exactly the search tool"
    write_notes(H.workspace(o.objective_id))
    src = ExplicitTestProposals([
        {"proposal_id": "p-search-1", "task_id": "TASK-0001", "contract_version": 1, "tool_id": n,
         "operation": "search", "arguments": {"path": "notes.json", "query": "budget"}, "target_resources": [],
         "expected_result": "matches", "referenced_requirement_ids": ["FR-01"]},
        {"proposal_id": "p-search-done", "task_id": "TASK-0001", "contract_version": 1, "claim": "complete",
         "evidence_ids": []}])
    H.h.controller.release(o.objective_id)
    state = H.h.controller.run_task(o.objective_id, "TASK-0001", src)
    assert state == "COMPLETED", H.h.store.task(o.objective_id, "TASK-0001")
    tools_seen = [t["tool_id"] for t in src.seen_packets[0].trusted["tools"]]
    assert tools_seen == [n], "the model sees only its allocated tools, never the installed catalogue"
    ver = H.h.store.verifications(o.objective_id, "TASK-0001")
    assert ver and all(v["passed"] for v in ver), ver
    assert "atta-integration-verify/1" in json.dumps(ver, default=str), "integration output verified independently"
    ev = [e["event_type"] for e in H.store.events(CONN)]
    assert {"tool_allocated", "execution_authorized_by_guard", "tool_executed"} <= set(ev)
    record("S12-T30", {"state": state, "pool": c.permitted_tool_ids, "events": ev, "verifications": ver})


def test_S12_T31_installation_is_not_global_availability_and_unassigned_tools_are_refused(tmp_path):
    H = build12(tmp_path, fixtures=["valid_connector", "valid_plugin"])
    H.install("valid_connector", enable=True)
    H.install("valid_plugin", enable=True)
    search, read = H.number(CONN, "search"), H.number(CONN, "read")
    summ = H.number(PLUG, "summarize")
    o = H.objective()
    c, ctx = start(H.h, o.objective_id, H.spec("TASK-0001", "connector.notes", ["search"], search))
    assert c.permitted_tool_ids == [search]
    write_notes(H.workspace(o.objective_id))
    out = {}
    for label, tool, op, args in (("same integration, other tool", read, "read", {"path": "notes.json", "note_id": "n1"}),
                                  ("other installed integration", summ, "read", {"text": "x"})):
        r = H.h.controller.gateway.execute(ctx, P("TASK-0001", tool, op, args))
        assert r.status == "rejected" and r.violations[0].rule == "tool_not_in_pool", r
        out[label] = r.violations[0].rule
    assert {e["event_type"] for e in H.store.events(CONN)} >= {"unassigned_tool_denied"}
    assert "unassigned_tool_denied" in {e["event_type"] for e in H.store.events(PLUG)}
    # a document task never gets integration tools, and a proposal to use one is refused by the allocator
    from backend.orchestration.controller import PlanningBlocked
    with pytest.raises(PlanningBlocked):
        H.h.controller.plan(o.objective_id, [H.spec("TASK-0002", "connector.notes", ["search"], search,
                                                    proposed_tool_ids=[search, summ])])
    record("S12-T31", out)


def test_S12_T32_policy_and_approval_still_apply_to_trusted_enabled_integrations(tmp_path):
    H = build12(tmp_path, fixtures=["valid_connector"])
    H.install("valid_connector", enable=True)
    create, send = H.number(CONN, "create"), H.number(CONN, "send")
    o = H.objective()
    c, ctx = start(H.h, o.objective_id, H.spec("TASK-0001", "connector.notes", ["create"], create,
                                                max_risk="medium"))
    assert c.permitted_tool_ids == [create]
    write_notes(H.workspace(o.objective_id))
    before = (H.workspace(o.objective_id) / "notes.json").read_text()
    r = H.h.controller.gateway.execute(ctx, P("TASK-0001", create, "create",
                                              {"path": "notes.json", "title": "t", "body": "b"}))
    assert r.status == "rejected" and r.violations[0].rule == "policy_denied" and "approval" in r.violations[0].detail
    assert (H.workspace(o.objective_id) / "notes.json").read_text() == before, "nothing executed"
    from backend.orchestration.controller import PlanningBlocked
    with pytest.raises(PlanningBlocked):    # high-risk SEND is not even allocatable to a low/medium-risk task
        H.h.controller.plan(o.objective_id, [H.spec("TASK-0002", "connector.notes", ["send"], send,
                                                    max_risk="medium")])
    ev = [e for e in H.store.events(CONN) if e["event_type"] == "approval_required"]
    assert ev and ev[0]["details"]["tool_id"] == create
    record("S12-T32", {"rule": r.violations[0].rule, "detail": r.violations[0].detail, "audit": ev[0]["details"]})


def test_S12_T33_prompt_injection_in_tool_output_stays_untrusted_data(tmp_path):
    H = build12(tmp_path, fixtures=["valid_plugin"])
    H.install("valid_plugin", enable=True)
    poison = H.number(PLUG, "poison")
    o = H.objective()
    spec = H.spec("TASK-0001", "plugin.text", ["read"], poison, paths=["."], proposed_tool_ids=[poison],
                  criteria=[{"id": "TC-01", "description": "executed", "requirement_ids": ["FR-01"],
                             "verifier": "execution_succeeded", "params": {"tool_id": poison}}])
    H.h.controller.plan(o.objective_id, [spec])
    c0 = H.h.store.contract(o.objective_id, "TASK-0001")
    seen = {}

    def second(packet, feedback):
        seen["packet"] = packet.as_dict()
        return {"proposal_id": "p-done-1", "task_id": "TASK-0001", "contract_version": 1, "claim": "complete",
                "evidence_ids": []}
    src = ExplicitTestProposals([
        {"proposal_id": "p-poison-1", "task_id": "TASK-0001", "contract_version": 1, "tool_id": poison,
         "operation": "read", "arguments": {}, "target_resources": [], "expected_result": "doc",
         "referenced_requirement_ids": ["FR-01"]}, second])
    H.h.controller.release(o.objective_id)
    state = H.h.controller.run_task(o.objective_id, "TASK-0001", src)
    pkt = seen["packet"]
    out = [u for u in pkt["untrusted"] if u["provenance"]["kind"] == "tool_output"]
    assert out and out[0]["provenance"]["tool_id"] == poison
    text = json.dumps(out[0]["content"])
    assert "Ignore previous instructions" in text, "hostile text is delivered as data, not dropped silently"
    assert "sk-THISISAFAKESECRET" not in json.dumps(pkt), "secret-looking values are redacted before the model"
    assert "Ignore previous" not in json.dumps(pkt["trusted"]), "hostile text never enters the trusted section"
    c1 = H.h.store.contract(o.objective_id, "TASK-0001")
    assert c1.contract_hash == c0.contract_hash and c1.permitted_tool_ids == [poison]
    b = H.h.store.budgets(o.objective_id, "TASK-0001")
    assert b["actions"]["limit"] == c0.maximum_actions, "budget unchanged"
    execs = H.h.store.executions(o.objective_id, "TASK-0001")
    assert [e["tool_number"] for e in execs] == [poison], "no other tool was run"
    rec = H.mgr.get_installed(PLUG)
    assert rec.trust_class.value == "TRUSTED" and rec.state.value == "ENABLED"
    flagged = [e for e in H.store.events(PLUG) if e["event_type"] == "tool_executed"][-1]["details"]
    assert flagged["injection_flags"] and flagged["secrets_redacted"] >= 1
    assert state == "COMPLETED"
    with H.h.store.db.tx() as x:
        dump = json.dumps(x.execute("SELECT details FROM integrations.integration_events WHERE namespace=%s",
                                    (H.h.namespace,)).fetchall(), default=str)
    assert "sk-THISISAFAKESECRET" not in dump
    record("S12-T33", {"flags": flagged["injection_flags"], "budgets": b, "contract_hash": c1.contract_hash,
                       "state": state})


def test_S12_T34_oversized_and_invalid_results_are_rejected_explicitly(tmp_path):
    H = build12(tmp_path, fixtures=["valid_plugin"])
    H.install("valid_plugin", enable=True)
    large, bad = H.number(PLUG, "large"), H.number(PLUG, "bad_output")
    o = H.objective()
    c, ctx = start(H.h, o.objective_id, H.spec("TASK-0001", "plugin.text", ["read"], large, paths=["."],
                                                proposed_tool_ids=[large, bad]))
    r = H.h.controller.gateway.execute(ctx, P("TASK-0001", large, "read", {"size": 100000}))
    assert r.status == "failed" and r.output["status"] == "result_too_large" and r.output["limit"] == 4096
    r2 = H.h.controller.gateway.execute(ctx, P("TASK-0001", large, "read", {"size": 100}))
    assert r2.status == "executed"
    r3 = H.h.controller.gateway.execute(ctx, P("TASK-0001", bad, "read", {}))
    assert r3.status == "failed" and r3.output["status"] == "invalid_output"
    ev = [e["details"]["reason"] for e in H.store.events(PLUG) if e["event_type"] == "result_rejected"]
    assert ev == ["result_too_large"]
    record("S12-T34", {"oversized": r.output, "invalid": r3.output})


def test_S12_T35_offline_mode_network_integrations_unavailable_local_ones_work(tmp_path, monkeypatch):
    H = build12(tmp_path, connectivity="AIR_GAPPED", fixtures=["valid_connector", "network_connector"])
    real = socket.socket.connect
    attempts = []

    def guard(self, addr):          # any non-loopback connection during install/execute is a hidden download/telemetry
        host = addr[0] if isinstance(addr, tuple) else str(addr)
        if host not in ("127.0.0.1", "::1", "localhost") and not str(host).startswith("/"):
            attempts.append(host)
            raise OSError("network disabled by test")
        return real(self, addr)
    monkeypatch.setattr(socket.socket, "connect", guard)
    H.install("valid_connector", enable=True)
    res = H.install("network_connector", enable=True)
    assert res["status"] == "INSTALLED"
    h = H.svc.health("test.remote_connector")
    assert h["status"] == "OFFLINE_UNAVAILABLE" and any("AIR_GAPPED" in r for r in h["reasons"])
    assert not H.h.registry.get("integration.test.remote_connector").executable
    n = H.number(CONN, "search")
    o = H.objective()
    c, ctx = start(H.h, o.objective_id, H.spec("TASK-0001", "connector.notes", ["search"], n))
    write_notes(H.workspace(o.objective_id))
    r = H.h.controller.gateway.execute(ctx, P("TASK-0001", n, "search", {"path": "notes.json", "query": "holiday"}))
    assert r.status == "executed" and r.output["count"] == 1, "local integration works while AIR_GAPPED"
    assert attempts == [], f"hidden network access attempted: {attempts}"
    H.mgr.network = NetworkPolicy(ConnectivityMode.ONLINE)
    h2 = H.svc.health("test.remote_connector")
    assert h2["status"] == "AUTH_REQUIRED", "back online it still needs Stage 02 credentials"
    record("S12-T35", {"offline": h, "online": h2, "local": r.output})


def test_S12_T36_credential_dependent_integrations_blocked_until_stage02(tmp_path):
    H = build12(tmp_path, fixtures=["network_connector"])
    res = H.install("network_connector", enable=True)
    refs = H.store.credential_refs("test.remote_connector")
    assert [r["ref"] for r in refs] == ["credref://test.remote_connector/api_token"]
    h = H.svc.health("test.remote_connector")
    assert h["status"] == "AUTH_REQUIRED"
    detail = H.svc.get("test.remote_connector")
    assert detail.credential_refs == ["credref://test.remote_connector/api_token"]
    assert "deferred" in json.dumps(res["verification"])
    with H.h.store.db.tx() as x:
        with pytest.raises(psycopg.Error):   # the database itself refuses anything that is not a reference
            x.execute("INSERT INTO integrations.integration_credentials_refs VALUES (%s,%s,%s,%s,%s,%s,%s)",
                      (H.h.namespace, "test.x", "1.0.0", "api_token", "bearer_token", "sk-rawsecretvalue", True))
    record("S12-T36", {"health": h, "refs": [r["ref"] for r in refs]})


def test_S12_T37_dynamic_health_and_audit_records(tmp_path):
    H = build12(tmp_path, fixtures=["valid_plugin"])
    H.install("valid_plugin")
    seq = [H.svc.health(PLUG)["status"]]
    H.enable(PLUG)
    seq.append(H.svc.health(PLUG)["status"])
    H.mgr.disable(PLUG, "admin")
    seq.append(H.svc.health(PLUG)["status"])
    H.enable(PLUG)
    seq.append(H.svc.health(PLUG)["status"])
    assert seq == ["STOPPED", "HEALTHY", "STOPPED", "HEALTHY"], seq
    hist = H.store.health_history(PLUG)
    assert len(hist) >= 4
    with pytest.raises(IntegrationError):
        H.svc.health("test.unknown_integration")
    types = {e["event_type"] for e in H.store.events(PLUG)}
    assert {"discovered", "validated", "install_approved", "installed", "enabled", "disabled"} <= types or \
        {"validated", "install_approved", "installed", "enabled", "disabled"} <= types
    with H.h.store.db.tx() as x:
        with pytest.raises(psycopg.Error):
            x.execute("UPDATE integrations.integration_events SET actor='x' WHERE namespace=%s", (H.h.namespace,))
    with H.h.store.db.tx() as x:
        with pytest.raises(psycopg.Error):
            x.execute("DELETE FROM integrations.installation_events WHERE namespace=%s", (H.h.namespace,))
    record("S12-T37", {"health_sequence": seq, "event_types": sorted(types)})
