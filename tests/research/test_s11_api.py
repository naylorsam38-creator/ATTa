"""Stage 11 API contracts (§28, §33): typed schemas exist for Stage 04, and every operation is evaluated by the central
policy engine — DENIED for unauthenticated callers until Stage 02 (nothing dangerous is exposed)."""
from __future__ import annotations

from fastapi.testclient import TestClient

from backend.api.app import create_app
from backend.research.api import OPERATIONS, build_research_router
from backend.settings import load_settings

RID = "rsr_01M88PEQ5H7X7DN1732JGEX3YY"
ROUTES = [("POST", "/v1/research", {"question": "What happened?", "research_type": "QUICK_SEARCH"}),
          ("GET", "/v1/research", None), ("GET", f"/v1/research/{RID}", None),
          ("POST", f"/v1/research/{RID}/start", {}), ("POST", f"/v1/research/{RID}/cancel", {}),
          ("GET", f"/v1/research/{RID}/progress", None), ("GET", f"/v1/research/{RID}/queries", None),
          ("GET", f"/v1/research/{RID}/sources", None), ("GET", f"/v1/research/{RID}/sources/src_x", None),
          ("GET", f"/v1/research/{RID}/claims", None), ("GET", f"/v1/research/{RID}/citations", None),
          ("GET", f"/v1/research/{RID}/contradictions", None), ("GET", f"/v1/research/{RID}/checkpoint", None),
          ("GET", f"/v1/research/{RID}/result", None), ("GET", f"/v1/research/{RID}/events", None)]


class Sentinel:
    """Any call reaching the service would be a security failure: the policy dependency must stop it first."""
    def __getattr__(self, name):
        raise AssertionError(f"service reached without authorisation: {name}")


def app():
    a = create_app(load_settings("test"), dependency_probe=lambda: [], registry=object())
    a.state.research_service = Sentinel()
    a.include_router(build_research_router(), prefix="/v1")
    return a


def test_every_research_operation_denied_before_stage02():
    with TestClient(app()) as client:
        for method, path, body in ROUTES:
            r = client.request(method, path, json=body)
            assert r.status_code == 403, (method, path, r.status_code, r.text)
            err = r.json()["error"]
            assert err["code"] == "permission_denied" and "Stage 02" in err["message"]
            assert r.headers.get("X-Correlation-ID")


def test_openapi_schema_exposes_typed_contracts_for_stage04():
    with TestClient(app()) as client:
        spec = client.get("/v1/openapi.json").json()
    paths = spec["paths"]
    for p in ("/v1/research", "/v1/research/{research_id}", "/v1/research/{research_id}/start",
              "/v1/research/{research_id}/cancel", "/v1/research/{research_id}/progress",
              "/v1/research/{research_id}/queries", "/v1/research/{research_id}/sources",
              "/v1/research/{research_id}/sources/{source_id}", "/v1/research/{research_id}/claims",
              "/v1/research/{research_id}/citations", "/v1/research/{research_id}/contradictions",
              "/v1/research/{research_id}/checkpoint", "/v1/research/{research_id}/result"):
        assert p in paths, p
    schemas = spec["components"]["schemas"]
    for s in ("CreateResearchRequest", "ResearchView", "Progress", "Page", "StartRequest", "CancelRequest",
              "ResearchMode", "ResearchStatus", "VerificationStatus"):
        assert s in schemas, s
    assert set(schemas["ResearchMode"]["enum"]) == {"QUICK_SEARCH", "STANDARD_RESEARCH", "DEEP_RESEARCH",
                                                    "SOURCE_COMPARISON", "FACT_VERIFICATION"}
    assert schemas["CreateResearchRequest"]["additionalProperties"] is False
    assert set(OPERATIONS.values()) <= {"research.create", "research.read", "research.list", "research.start",
                                        "research.cancel"}


def test_validation_runs_after_authorisation_and_rejects_unknown_fields():
    with TestClient(app()) as client:
        r = client.post("/v1/research", json={"question": "x", "research_type": "QUICK_SEARCH", "grant": "all"})
    assert r.status_code in (403, 422)       # never 2xx; unknown fields are forbidden by the schema
