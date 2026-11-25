"""Stage 12 — MCP client architecture: negotiation, discovery, schema validation, malicious metadata, timeouts,
cancellation, failure isolation, HTTP transport under network policy, dynamic health."""
from __future__ import annotations

import shutil
import sys
import threading
import time

import psutil
import pytest

from stage12_harness import P, build12, record, start

from backend.integrations.manifest import write_integrity
from backend.integrations.mcp import wire
from mcp_http_fixture import HttpMcp, http_manifest

pytestmark = pytest.mark.requires_services
MCP = "test.echo_mcp"


def _server_procs(marker: str) -> list:
    out = []
    for p in psutil.process_iter(["cmdline"]):
        try:
            if any(marker in a for a in (p.info["cmdline"] or [])):
                out.append(p)
        except psutil.Error:
            pass
    return out


def test_S12_T40_mcp_negotiation_and_discovery(tmp_path):
    H = build12(tmp_path, fixtures=["valid_mcp"])
    res = H.install("valid_mcp", enable=True)
    assert res["status"] == "INSTALLED", res
    assert res["mcp"]["protocol_version"] == "2025-06-18"
    srv = H.store.mcp_server(MCP)
    assert srv["lifecycle"] == "HEALTHY" and srv["protocol_version"] == "2025-06-18"
    caps = H.store.mcp_capabilities(MCP)
    tools = {c["name"]: c for c in caps if c["kind"] == "tool"}
    assert set(tools) == {"echo", "add", "slow", "inject", "big", "fail", "probe_env", "net_probe", "ask_client",
                          "admin_reset"} and all(c["accepted"] for c in tools.values())
    assert [c["name"] for c in caps if c["kind"] == "resource"] == ["test://notes/1"]
    assert [c["name"] for c in caps if c["kind"] == "prompt"] == ["summarise"]
    assert H.svc.health(MCP)["status"] == "HEALTHY"
    n = H.number(MCP, "add")
    assert n.startswith("022.5")
    o = H.objective()
    c, ctx = start(H.h, o.objective_id, H.spec("TASK-0001", "mcp.test", ["read"], n, paths=["."],
                                                proposed_tool_ids=[n]))
    r = H.h.controller.gateway.execute(ctx, P("TASK-0001", n, "read", {"a": 2, "b": 3}))
    assert r.status == "executed" and r.output["structured_content"] == {"sum": 5}, r
    bad = H.h.controller.gateway.execute(ctx, P("TASK-0001", n, "read", {"a": "two", "b": 3}))
    assert bad.status == "rejected" and bad.violations[0].rule == "invalid_arguments", "validated before the server"
    assert _server_procs(str(H.mgr.packages_dir)) == [], "every MCP server process was terminated"
    record("S12-T40", {"install": res["mcp"], "result": r.output, "capabilities": [c["name"] for c in caps]})


def test_S12_T41_mcp_schema_drift_and_malicious_metadata_are_refused(tmp_path):
    H = build12(tmp_path, fixtures=["valid_mcp", "malicious_mcp", "drifting_mcp"])
    evil = H.install("malicious_mcp")
    assert evil["status"] == "BLOCKED" and any("malicious" in p for p in evil["problems"]), evil
    assert H.h.registry.get("integration.test.evil_mcp") is None, "nothing from the malicious server registered"
    assert H.store.integration("test.evil_mcp")["state"] == "BLOCKED"
    caps = {c["name"]: c for c in H.store.mcp_capabilities("test.evil_mcp")}
    assert caps["hidden_admin"]["accepted"] is False, "undeclared tool never registered"
    srv = H.store.mcp_server("test.evil_mcp")
    assert srv["lifecycle"] in ("BLOCKED", "UNHEALTHY")
    assert srv["server_info"]["instructions_ignored"] is True
    import json as _json
    assert "system prompt" not in _json.dumps(srv, default=str), "server instructions are never stored or forwarded"
    drift = H.install("drifting_mcp")
    assert drift["status"] == "FAILED" and any("differs from the approved manifest" in p for p in drift["problems"])
    assert H.h.registry.get("integration.test.drift_mcp") is None
    ok = H.install("valid_mcp", enable=True)            # unaffected by the failures above
    assert ok["status"] == "INSTALLED" and H.svc.health(MCP)["status"] == "HEALTHY"
    record("S12-T41", {"malicious": evil["problems"], "drift": drift["problems"]})


def test_S12_T42_mcp_host_client_timeout_cancellation_and_server_requests(tmp_path):
    pkg = tmp_path / "srv"
    shutil.copytree(H_FIX := (__import__("stage12_harness").FIX / "valid_mcp"), pkg)
    argv = [sys.executable, "-I", str(pkg / "server.py"), "normal"]
    s = wire.McpSession(wire.StdioConnection(argv, cwd=str(pkg)), request_timeout_s=5)
    s.initialize()
    t0 = time.monotonic()
    with pytest.raises(wire.McpTimeout):
        s.call_tool("slow", {"seconds": 10}, timeout=0.7)
    assert time.monotonic() - t0 < 3
    proc = s.conn.proc
    s.close()
    assert proc.poll() is not None, "server process terminated on close"
    s2 = wire.McpSession(wire.StdioConnection(argv, cwd=str(pkg)), request_timeout_s=30)
    s2.initialize()
    threading.Timer(0.5, s2.cancel).start()
    t0 = time.monotonic()
    with pytest.raises(wire.McpCancelled):
        s2.call_tool("slow", {"seconds": 10})
    assert time.monotonic() - t0 < 3
    s2.close()
    with pytest.raises(wire.McpTransportError):
        wire.StdioConnection("python server.py")      # a shell string is never accepted
    record("S12-T42", {"timeout": "McpTimeout", "cancel": "McpCancelled", "fixture": str(H_FIX)})


def test_S12_T43_mcp_tool_timeout_and_cancellation_through_the_gateway(tmp_path):
    H = build12(tmp_path, fixtures=["valid_mcp"])
    H.install("valid_mcp", enable=True)
    n = H.number(MCP, "slow")
    o = H.objective()
    c, ctx = start(H.h, o.objective_id, H.spec("TASK-0001", "mcp.test", ["read"], n, paths=["."],
                                                proposed_tool_ids=[n], execution_deadline=4))
    t0 = time.monotonic()
    r = H.h.controller.gateway.execute(ctx, P("TASK-0001", n, "read", {"seconds": 30}))
    took = time.monotonic() - t0
    assert r.status == "failed" and r.output["status"] == "timeout" and took < 15, (r, took)
    assert _server_procs(str(H.mgr.packages_dir)) == []
    o2 = H.objective()
    c2, ctx2 = start(H.h, o2.objective_id, H.spec("TASK-0002", "mcp.test", ["read"], n, paths=["."],
                                                  proposed_tool_ids=[n]))
    box = {}
    th = threading.Thread(target=lambda: box.update(r=H.h.controller.gateway.execute(
        ctx2, P("TASK-0002", n, "read", {"seconds": 30}))))
    th.start()
    eid = None
    for _ in range(100):
        rows = [e for e in H.h.store.executions(o2.objective_id, "TASK-0002") if e["state"] == "running"]
        if rows:
            eid = rows[0]["execution_id"]
            break
        time.sleep(0.1)
    assert eid
    time.sleep(1.0)
    assert H.h.controller.gateway.cancel(eid)
    th.join(20)
    assert box["r"].status == "failed" and box["r"].output["status"] == "cancelled", box
    time.sleep(0.5)
    assert _server_procs(str(H.mgr.packages_dir)) == []
    record("S12-T43", {"timeout": r.output, "cancelled": box["r"].output, "timeout_seconds": round(took, 2)})


def test_S12_T44_mcp_failure_isolation_and_no_fallback(tmp_path):
    H = build12(tmp_path, fixtures=["valid_mcp", "valid_plugin"])
    crash = H.src / "crashing_mcp"
    shutil.copytree(H.src / "valid_mcp", crash)
    (crash / "server.py").write_text("import sys\nsys.exit(3)\n")
    import yaml
    m = yaml.safe_load((crash / "integration.yaml").read_text())
    m["integration_id"] = "test.crashing_mcp"
    (crash / "integration.yaml").write_text(yaml.safe_dump(m))
    write_integrity(crash)
    H.install("valid_mcp", enable=True)
    H.install("valid_plugin", enable=True)
    bad = H.install("crashing_mcp")
    assert bad["status"] == "FAILED" and any("negotiation failed" in p for p in bad["problems"]), bad
    assert sorted(x for x in H.h.registry.executable_ids() if x.startswith("integration.")) == \
        ["integration.test.echo_mcp", "integration.test.text_plugin"]
    fail, echo = H.number(MCP, "fail"), H.number(MCP, "echo")
    o = H.objective()
    c, ctx = start(H.h, o.objective_id, H.spec("TASK-0001", "mcp.test", ["read"], fail, paths=["."],
                                                proposed_tool_ids=[fail, echo]))
    r = H.h.controller.gateway.execute(ctx, P("TASK-0001", fail, "read", {}))
    assert r.status == "failed" and "MCP tool reported an error" in (r.output.get("error") or ""), r
    execs = H.h.store.executions(o.objective_id, "TASK-0001")
    assert [e["tool_number"] for e in execs] == [fail], "no silent retry or redirect to another tool/provider"
    r2 = H.h.controller.gateway.execute(ctx, P("TASK-0001", echo, "read", {"text": "still works"}))
    assert r2.status == "executed" and r2.output["content"][0]["text"] == "still works"
    record("S12-T44", {"crashing_install": bad["problems"], "failed_call": r.output})


def test_S12_T45_http_transport_network_policy_and_dynamic_health(tmp_path):
    srv = HttpMcp()
    try:
        H = build12(tmp_path, connectivity="AIR_GAPPED", fixtures=[])
        res = H.install(http_manifest(srv.port), enable=True)
        assert res["status"] == "INSTALLED", res
        assert H.svc.health("test.http_mcp")["status"] == "HEALTHY", "loopback is allowed while AIR_GAPPED"
        n = H.number("test.http_mcp", "echo")
        o = H.objective()
        c0, ctx0 = start(H.h, o.objective_id, H.spec("TASK-0001", "mcp.test", ["read"], n, paths=["."],
                                                      proposed_tool_ids=[n]))
        r0 = H.h.controller.gateway.execute(ctx0, P("TASK-0001", n, "read", {"text": "no destination"}))
        assert r0.status == "rejected" and r0.violations[0].rule == "integration_network_not_permitted", r0
        o = H.objective()
        c, ctx = start(H.h, o.objective_id, H.spec("TASK-0002", "mcp.test", ["read"], n, paths=["."],
                                                    proposed_tool_ids=[n], permitted_network_destinations=["127.0.0.1"]))
        r = H.h.controller.gateway.execute(ctx, P("TASK-0002", n, "read", {"text": "over http"}))
        assert r.status == "executed" and r.output["content"][0]["text"] == "over http"
        hdrs = [{k.lower(): v for k, v in q["headers"].items()} for q in srv.requests]
        assert any(h.get("mcp-session-id") == "test-session-1" for h in hdrs), hdrs
        assert any(h.get("mcp-protocol-version") == "2025-06-18" for h in hdrs), hdrs
        srv.stop()
        h_down = H.svc.health("test.http_mcp")
        assert h_down["status"] == "UNHEALTHY", h_down
        srv2 = HttpMcp()
        remote = H.install(http_manifest(srv2.port, iid="test.remote_mcp", host="mcp.example.com"))
        srv2.stop()
        assert remote["status"] == "INSTALLED" and remote["mcp"]["negotiation"] == "deferred"
        assert H.svc.health("test.remote_mcp")["status"] in ("STOPPED", "OFFLINE_UNAVAILABLE")
        H.enable("test.remote_mcp")
        assert H.svc.health("test.remote_mcp")["status"] == "OFFLINE_UNAVAILABLE"
        record("S12-T45", {"install": res["mcp"], "down": h_down, "remote": remote["mcp"]})
    finally:
        try:
            srv.stop()
        except Exception:  # noqa: BLE001
            pass
