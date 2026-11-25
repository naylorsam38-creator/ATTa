"""Stage 12 §9 (final package): MCP servers cannot bypass task allocation, permissions/approvals, credential
restrictions or network policy. Every call goes through the Stage 01A controller/gateway."""
from __future__ import annotations

import json
import re
import shutil
import socket
import threading

import pytest
import yaml

from stage12_harness import P, build12, record, stage01b_runtime_present, start

from backend.capability_runtime import ExecutionTicket, RuntimeRefused
from backend.integrations.manifest import write_integrity
from mcp_http_fixture import HttpMcp, http_manifest

pytestmark = pytest.mark.requires_services
MCP = "test.echo_mcp"
SECRET_ENV = re.compile(r"(?i)(^ATTA|PASSWORD|SECRET|TOKEN|API_?KEY|^PG|DATABASE|^AWS_|CREDENTIAL|PRIVATE)")


def _task(H, tid, n, **kw):
    o = H.objective()
    c, ctx = start(H.h, o.objective_id, H.spec(tid, "mcp.test", ["read"], n, paths=["."], proposed_tool_ids=[n], **kw))
    return o, c, ctx


def test_S12_T46_mcp_cannot_bypass_allocation_permissions_or_reach_client_capabilities(tmp_path, monkeypatch):
    monkeypatch.setenv("ATTA_FAKE_SERVICE_PASSWORD", "must-never-reach-mcp")
    monkeypatch.setenv("ATTA_TEST_TOKEN", "sk-shouldnotleak0123456789abcd")
    H = build12(tmp_path, fixtures=["valid_mcp"])
    H.install("valid_mcp", enable=True)
    echo, probe, net, ask, admin = (H.number(MCP, t) for t in ("echo", "probe_env", "net_probe", "ask_client",
                                                                "admin_reset"))
    out = {}
    o, c, ctx = _task(H, "TASK-0001", echo)
    assert c.permitted_tool_ids == [echo]
    for other in (probe, net, admin):            # (a) allocation: other tools of the SAME server are refused
        r = H.h.controller.gateway.execute(ctx, P("TASK-0001", other, "read", {}))
        assert r.status == "rejected" and r.violations[0].rule == "tool_not_in_pool", r
    out["allocation"] = "tool_not_in_pool for every unallocated tool of the server"
    oa, ca, ctxa = _task(H, "TASK-0002", admin, max_risk="high")    # (b) ADMINISTRATIVE/high: approval required
    r = H.h.controller.gateway.execute(ctxa, P("TASK-0002", admin, "read", {}))
    assert r.status == "rejected" and r.violations[0].rule == "policy_denied" and "approval" in r.violations[0].detail
    assert H.h.store.executions(oa.objective_id, "TASK-0002") == [], "nothing reached the server"
    out["approval"] = r.violations[0].detail
    op, cp, ctxp = _task(H, "TASK-0003", probe)      # (c) the server process never sees ATTa secrets
    r = H.h.controller.gateway.execute(ctxp, P("TASK-0003", probe, "read", {}))
    assert r.status == "executed", r
    keys = r.output["structured_content"]["keys"]
    leaked = [k for k in keys if SECRET_ENV.search(k)]
    assert leaked == [], leaked
    out["server_env_keys"] = keys
    oq, cq, ctxq = _task(H, "TASK-0004", ask)        # (d) server->client requests (sampling, roots) are refused
    r = H.h.controller.gateway.execute(ctxq, P("TASK-0004", ask, "read", {}))
    assert r.status == "executed", r
    answers = r.output["structured_content"]["answers"]
    assert [a["error"]["code"] for a in answers] == [-32601, -32601] and all("result" not in a for a in answers)
    out["client_requests"] = answers
    rt = H.h.controller.runtime                       # (e) no execution without a gateway-signed ticket
    with pytest.raises(RuntimeRefused):
        rt.issue_ticket("exe_x", "integration.test.echo_mcp", "execute")
    forged = ExecutionTicket("exe_forged", "integration.test.echo_mcp", "execute", 9e12, "AAAA")
    reg = H.h.registry.get("integration.test.echo_mcp")
    with pytest.raises(RuntimeRefused):
        rt.run(ticket=forged, mode="execute", capability_id=reg.id, package_dir=reg.manifest.directory,
               expected_digest=reg.package_digest, handlers=reg.manifest.handler_refs, timeout_s=5, payload={})
    out["direct_runtime"] = "RuntimeRefused"
    record("S12-T46", out)


def test_S12_T47_mcp_credentials_and_network_restrictions(tmp_path):
    H = build12(tmp_path, fixtures=["valid_mcp"])
    d = H.src / "cred_mcp"                           # (f) an MCP server that needs a credential stays blocked
    shutil.copytree(H.src / "valid_mcp", d)
    m = yaml.safe_load((d / "integration.yaml").read_text())
    m["integration_id"] = "test.cred_mcp"
    m["credential_requirements"] = [{"name": "api_key", "kind": "api_key", "description": "server key",
                                     "required": True}]
    (d / "integration.yaml").write_text(yaml.safe_dump(m))
    write_integrity(d)
    H.install("cred_mcp", enable=True)
    h = H.svc.health("test.cred_mcp")
    assert h["status"] == "AUTH_REQUIRED"
    n = H.number("test.cred_mcp", "echo")
    from backend.orchestration.controller import PlanningBlocked
    o = H.objective()
    with pytest.raises(PlanningBlocked):            # not executable -> never allocatable
        H.h.controller.plan(o.objective_id, [H.spec("TASK-0001", "mcp.test", ["read"], n, paths=["."],
                                                    proposed_tool_ids=[n])])
    target = HttpMcp()                               # (g) a redirect can never move a call to another host
    srv = HttpMcp(redirect_to=f"http://127.0.0.1:{target.port}/mcp")
    try:
        res = H.install(http_manifest(srv.port, iid="test.redirect_mcp"), enable=True)
        assert res["status"] == "INSTALLED"
        n2 = H.number("test.redirect_mcp", "echo")
        o2, c2, ctx2 = _task(H, "TASK-0002", n2, permitted_network_destinations=["127.0.0.1"])
        r = H.h.controller.gateway.execute(ctx2, P("TASK-0002", n2, "read", {"text": "x"}))
        assert r.status == "failed" and "redirect" in (r.output.get("error") or ""), r
        assert target.requests == [], "the redirect target was never contacted"
    finally:
        srv.stop()
        target.stop()
    record("S12-T47", {"credential_health": h, "redirect": r.output})


@pytest.mark.skipif(not stage01b_runtime_present(),
                    reason="BLOCKED: requires Worker A's Stage 01B isolating runtime (run in the 01B trial tree)")
def test_S12_T48_mcp_stdio_server_confined_by_stage01b_network_namespace(tmp_path):
    lst = socket.socket()
    lst.bind(("127.0.0.1", 0))
    lst.listen(5)
    port = lst.getsockname()[1]
    threading.Thread(target=lambda: [lst.accept()[0].close() for _ in range(5)], daemon=True).start()
    H = build12(tmp_path, fixtures=["valid_mcp", "valid_plugin"], worker_network="none")
    assert H.mgr.isolation.report().available, H.mgr.isolation.report()
    H.install("valid_mcp", enable=True)
    echo, net = H.number(MCP, "echo"), H.number(MCP, "net_probe")
    o, c, ctx = _task(H, "TASK-0001", echo)
    r = H.h.controller.gateway.execute(ctx, P("TASK-0001", echo, "read", {"text": "inside the namespace"}))
    assert r.status == "executed" and r.output["content"][0]["text"] == "inside the namespace", r
    o2, c2, ctx2 = _task(H, "TASK-0002", net)
    r2 = H.h.controller.gateway.execute(ctx2, P("TASK-0002", net, "read", {"host": "127.0.0.1", "port": port}))
    assert r2.status == "executed" and r2.output["structured_content"]["result"].startswith("refused"), r2
    ev = H.h.store.evidence_for(o2.objective_id, "TASK-0002")
    iso = [e for e in ev if e["kind"] == "tool_execution"][-1]["content"].get("isolation", {})
    res = H.install("valid_plugin", trust=__import__("stage12_harness").TrustClass.RESTRICTED, enable=True)
    assert H.svc.health("test.text_plugin")["status"] == "HEALTHY", "RESTRICTED runs under real 01B isolation"
    lst.close()
    record("S12-T48", {"echo": r.output, "net_probe": r2.output, "isolation_applied": iso,
                       "restricted_plugin": res["status"], "isolation_report": H.mgr.isolation.report().__dict__})
