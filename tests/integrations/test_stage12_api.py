"""Stage 12 — typed backend contracts and /v1 routes (default DENY until Stage 02, like every protected route)."""
from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from stage12_harness import ROOT, build12, record

from backend.api.app import create_app
from backend.integrations.service import IntegrationDetail, ToolInfo

pytestmark = pytest.mark.requires_services
ROUTES = [("get", "/v1/integrations"), ("post", "/v1/integrations/discover"), ("post", "/v1/integrations/validate"),
          ("post", "/v1/integrations/install"), ("get", "/v1/integrations/test.notes_connector"),
          ("post", "/v1/integrations/test.notes_connector/enable"),
          ("post", "/v1/integrations/test.notes_connector/disable"),
          ("post", "/v1/integrations/test.notes_connector/upgrade"),
          ("delete", "/v1/integrations/test.notes_connector"), ("get", "/v1/integrations/test.notes_connector/health"),
          ("get", "/v1/integrations/test.notes_connector/configuration-schema"),
          ("get", "/v1/integrations/test.notes_connector/capabilities"),
          ("get", "/v1/integrations/test.notes_connector/tools"),
          ("get", "/v1/integrations/test.notes_connector/permissions"),
          ("get", "/v1/integrations/test.notes_connector/events"), ("get", "/v1/mcp/servers"),
          ("get", "/v1/mcp/servers/test.echo_mcp/capabilities"), ("get", "/v1/skills"), ("get", "/v1/skills/scripts"),
          ("get", "/v1/plugins")]


def test_S12_T60_routes_exist_and_are_denied_until_stage02(tmp_path):
    H = build12(tmp_path, fixtures=["valid_connector"])
    H.install("valid_connector", enable=True)
    app = create_app(H.h.settings, policy=H.h.controller.policy, registry=H.h.registry, dependency_probe=lambda: [])
    app.state.integrations = H.svc
    c = TestClient(app)
    out = {}
    for method, url in ROUTES:
        kw = {"json": {"source": "x"}} if method == "post" else {}
        r = getattr(c, method)(url, **kw)
        out[f"{method.upper()} {url}"] = r.status_code
        assert r.status_code == 403 and r.json()["error"]["code"] == "permission_denied", (url, r.text)
    spec = c.get("/v1/openapi.json").json()
    paths = set(spec["paths"])
    for _, url in ROUTES:
        templ = url.replace("test.notes_connector", "{integration_id}").replace("test.echo_mcp", "{integration_id}")
        assert templ in paths, templ
    assert H.mgr.get_installed("test.notes_connector").state.value == "ENABLED", "nothing changed"
    record("S12-T60", out)


def test_S12_T61_service_contracts_carry_the_unified_descriptor(tmp_path):
    H = build12(tmp_path, fixtures=["valid_connector", "network_connector"])
    H.install("valid_connector", enable=True)
    H.install("network_connector")
    d = H.svc.get("test.notes_connector")
    assert isinstance(d, IntegrationDetail)
    dd = d.model_dump()
    for f in ("integration_id", "integration_type", "name", "version", "provider", "description", "manifest_version",
              "capabilities", "tools", "permissions_requested", "network_requirements", "credential_requirements",
              "risk_class", "trust_class", "execution_mode", "configuration_schema", "enabled", "installed_at",
              "updated_at", "integrity"):
        assert f in dd, f
    assert dd["risk_class"] == "high" and dd["enabled"] and dd["integrity"]["package_digest"]
    tools = H.svc.tools("test.notes_connector")
    assert all(isinstance(t, ToolInfo) for t in tools)
    by = {t.name: t for t in tools}
    assert by["search"].side_effect == "READ_ONLY" and not by["search"].approval_required
    assert by["send"].side_effect == "EXTERNAL_COMMUNICATION" and by["send"].approval_required
    assert all(t.error_schema and t.input_schema and t.output_schema for t in tools)
    nd = H.svc.get("test.remote_connector")
    assert nd.credential_refs == ["credref://test.remote_connector/api_token"]
    assert "api_token" not in json.dumps(nd.configuration_schema)
    listing = H.svc.list()
    assert {x.integration_id for x in listing} >= {"test.notes_connector", "test.remote_connector"}
    gen = ROOT / "packages" / "shared_types" / "integrations.contracts.generated.json"
    assert gen.is_file(), "run scripts/export_integration_contracts.py"
    current = json.loads(gen.read_text())
    assert current["IntegrationDetail"] == IntegrationDetail.model_json_schema()
    record("S12-T61", {"detail": dd["integration_id"], "tools": [t.model_dump()["tool_id"] for t in tools]})
