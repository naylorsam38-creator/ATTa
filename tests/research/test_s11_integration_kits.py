"""Controlled-integration kits: Stage 03 hub contract, Stage 04 API wiring, Stage 02 permission registration,
search-provider configuration. The hub here is a labelled TEST FIXTURE (FakeHub), not a model."""
from __future__ import annotations

import shutil

import pytest
import yaml
from fastapi.testclient import TestClient

from backend.api.app import create_app
from backend.policy_engine import PolicyEngine
from backend.policy_engine.engine import Decision
from backend.research import operator_config, provider_check
from backend.research.hub import HubClaimBuilder, HubPlanner, validate_synthesis
from backend.research.wiring import wire_research_api
from backend.settings import load_settings
from conftest import requires_db
from harness import ROOT, build, record

Q = "When did the Rivertown bridge reopen and what did the repairs cost?"


class FakeHub:
    """TEST FIXTURE standing in for a Stage 03 hub. Deliberately misbehaves to prove validation."""
    name = "fake-test-hub"

    def __init__(self, synth="good"):
        self.synth = synth
        self.calls = []

    def available(self):
        return True

    def plan_subquestions(self, contract):
        self.calls.append("plan")
        return ["Rivertown bridge reopen date", "Rivertown bridge repair cost", 42, "", "x" * 900] + \
            [f"extra aspect {i}" for i in range(30)]

    def formulate_query(self, contract, sub, attempt):
        self.calls.append(("query", attempt))
        return "  " + sub + "   bridge  "

    def build_claims(self, contract, packet):
        a = next(s["source_id"] for s in packet["sources"] if s["canonical_url"].endswith("/page/a.html"))
        return [{"text": "The bridge reopened on Monday.", "builder": "explicit-test",          # spoofed label
                 "support": [{"source_id": a, "quote": "The Rivertown bridge reopened on Monday"}]},
                {"text": "Invented citation.", "support": [{"evidence_id": "rev_01ZZZZZZZZZZZZZZZZZZZZZZZZ"}]},
                "not a dict"]

    def synthesise(self, contract, material):
        cid = material["claims"][0]["citation_ids"][0]
        if self.synth == "good":
            return {"text": f"The bridge reopened on Monday [{cid}].", "citation_ids": [cid]}
        return {"text": "It cost 99 million [cit_01ZZZZZZZZZZZZZZZZZZZZZZZZ].",
                "citation_ids": ["cit_01ZZZZZZZZZZZZZZZZZZZZZZZZ"]}


# ------------------------------------------------------------------------------------------------ Stage 03 hub
def test_hub_planner_bounds_and_validates_output():
    from test_s11_contracts import contract
    from backend.research.contracts import ResearchMode
    c = contract(mode=ResearchMode.STANDARD_RESEARCH)
    subs = HubPlanner(FakeHub()).decompose(c)
    assert len(subs) == c.execution_budget.max_subquestions and all(isinstance(s, str) and 3 <= len(s) <= 500
                                                                    for s in subs)
    hp = HubPlanner(FakeHub())
    assert hp.query_for(c, "abc def", 0) == "abc def bridge" and hp.query_for(c, "abc", 2) is None


def test_validate_synthesis_rules():
    from backend.research.ids import rid
    a, b = rid("cit"), rid("cit")
    good = {"text": f"Fact [{a}].", "citation_ids": [a]}
    ok, why = validate_synthesis(good, {a}, require_citations=True)
    assert ok and not why
    for bad in ({"text": "uncited claim", "citation_ids": []},
                {"text": f"x [{b}]", "citation_ids": [b]},
                {"text": f"x [{a}]", "citation_ids": []}, "not a dict"):
        ok, why = validate_synthesis(bad, {a}, require_citations=True)
        assert ok is None and why


@requires_db
def test_hub_integration_end_to_end_with_validated_synthesis(tmp_path, fx):
    hub = FakeHub()
    h = build(tmp_path, port=fx.port, hub=hub)
    c = h.service.create(Q, "STANDARD_RESEARCH")
    assert c.subquestions and len(c.subquestions) <= c.execution_budget.max_subquestions   # stored once
    h.approve(c.research_id)
    r = h.service.run(c.research_id)
    claims = h.store.claims(c.research_id)
    assert len(claims) == 1 and claims[0]["builder"] == "hub:fake-test-hub"      # label cannot be spoofed
    assert any(l["code"] == "VERIFICATION_FAILED" and l.get("rejected") for l in r["limitations"])   # invented cit
    assert r["synthesis"]["status"] == "MODEL" and r["synthesis"]["citation_ids"]
    assert not any(l["code"] == "BLOCKED_DEPENDENCY" and l.get("dependency") == "S03" for l in r["limitations"])
    assert r["verification_status"] == "VERIFIED"
    record("S11-HUB-INTEGRATION", {"synthesis": r["synthesis"], "calls": [str(x) for x in hub.calls]})


@requires_db
def test_hub_synthesis_with_unverified_citation_is_rejected(tmp_path, fx):
    h = build(tmp_path, port=fx.port, hub=FakeHub(synth="bad"))
    c = h.service.create(Q, "STANDARD_RESEARCH")
    h.approve(c.research_id)
    r = h.service.run(c.research_id)
    assert r["synthesis"]["status"] == "REJECTED" and r["synthesis"]["natural_language"] is None
    assert any(l["code"] == "VERIFICATION_FAILED" and "synthesis" in l["detail"] for l in r["limitations"])


def test_without_hub_model_features_stay_blocked():
    from backend.research.errors import ResearchError, ResearchErrorCode
    with pytest.raises(ResearchError) as e:
        HubClaimBuilder(None).build(None, {})
    assert e.value.code is ResearchErrorCode.BLOCKED_DEPENDENCY


# ------------------------------------------------------------------------------------------------ Stage 04 wiring
class Stage02Simulation(PolicyEngine):
    """TEST ONLY: simulates a Stage 02 grant of the research.* API operations to show the wiring is complete."""
    def evaluate(self, req):
        if req.operation.startswith("research."):
            return self._decide(Decision.ALLOW, "simulated Stage 02 grant (test)", req)
        return super().evaluate(req)


def _app(policy=None):
    return create_app(load_settings("test"), policy=policy, dependency_probe=lambda: [], registry=object())


def test_wiring_is_idempotent_and_denied_before_stage02():
    app = _app()
    assert wire_research_api(app) is True
    n = len(app.routes)
    assert wire_research_api(app) is False and len(app.routes) == n          # second call adds nothing
    spec = app.openapi()["paths"]
    assert set(spec["/v1/research"]) == {"get", "post"}
    with TestClient(app) as client:
        r = client.get("/v1/research")
    assert r.status_code == 403


def test_wiring_without_controller_reports_not_ready():
    app = _app(Stage02Simulation())
    wire_research_api(app)
    with TestClient(app) as client:
        r = client.get("/v1/research")
    assert r.status_code == 503 and r.json()["error"]["code"] == "not_ready"


@requires_db
def test_wiring_end_to_end_with_simulated_stage02_grant(h, fx):
    app = _app(Stage02Simulation())
    wire_research_api(app, service=h.service)
    with TestClient(app) as client:
        created = client.post("/v1/research", json={"question": "Rivertown bridge", "research_type": "QUICK_SEARCH"})
        assert created.status_code == 201, created.text
        rid = created.json()["research_id"]
        assert created.json()["approval_required"]["action"] == "approve_objective"
        h.approve(rid)
        h.service.run(rid)
        for path in ("", "/progress", "/queries", "/sources", "/claims", "/citations", "/contradictions",
                     "/checkpoint", "/result", "/events"):
            r = client.get(f"/v1/research/{rid}{path}")
            assert r.status_code == 200, (path, r.text)
        assert client.get(f"/v1/research/{rid}/result").json()["research_status"] in (
            "COMPLETED", "COMPLETED_WITH_LIMITATIONS")
        assert client.post(f"/v1/research/{rid}/start", json={}).status_code == 403   # execution stays Stage 02
        first = client.get("/v1/research", params={"limit": 5}).json()["items"]
        assert [i["research_id"] for i in first] == sorted((i["research_id"] for i in first), reverse=True)
        assert first[0]["research_id"] >= rid                            # history: newest first (shared DB)
        ids, cursor = [], None
        for _ in range(200):                                             # cursor pagination reaches every run
            page = client.get("/v1/research", params={"limit": 50, **({"cursor": cursor} if cursor else {})}).json()
            ids += [i["research_id"] for i in page["items"]]
            cursor = page["next_cursor"]
            if not cursor:
                break
        assert rid in ids and len(ids) == len(set(ids)) and ids == sorted(ids, reverse=True)


# ------------------------------------------------------------------------------------------------ Stage 02 registration
def test_operator_config_check_and_render(tmp_path):
    assert operator_config.main(["check"]) == 0
    out = tmp_path / "out"
    assert operator_config.main(["render", "--out", str(out)]) == 0
    nums = yaml.safe_load((out / "tool_numbers.yaml").read_text())["tools"]
    assert nums["040.001"]["tool"] == "ops.system_status:report" and nums["011.002"]["tool"] == "research.web:fetch_source"
    from backend.policy_engine.catalog import load_catalog
    cat = load_catalog(out / "permissions.yaml")
    assert {"research.search.query", "network.web.read"} <= cat.controller_granted
    assert operator_config.main(["render", "--out", str(ROOT / "configuration")]) == 2      # never in place


def test_operator_config_detects_conflicts(tmp_path):
    cfg = tmp_path / "configuration"
    shutil.copytree(ROOT / "configuration", cfg)
    doc = yaml.safe_load((cfg / "tool_numbers.yaml").read_text())
    doc["tools"]["011.001"] = {"tool": "someone.else:tool"}
    (cfg / "tool_numbers.yaml").write_text(yaml.safe_dump(doc))
    assert operator_config.main(["check", "--config-dir", str(cfg)]) == 1


def test_stage02_permission_registration_covers_every_api_operation():
    from backend.research.api import OPERATIONS
    doc = yaml.safe_load((ROOT / "documentation/stage11/integration/stage02_research_permissions.yaml").read_text())
    assert set(OPERATIONS.values()) <= set(doc["api_operations"])
    assert all(v["permission"] in doc["permissions"] for v in doc["api_operations"].values())


# ------------------------------------------------------------------------------------------------ provider config
def test_provider_check(tmp_path):
    assert provider_check.main([]) == 0                       # shipped config: valid, zero providers (BLOCKED)
    base = yaml.safe_load((ROOT / "configuration/research.yaml").read_text())
    ex = yaml.safe_load((ROOT / "documentation/stage11/integration/research.providers.example.yaml").read_text())
    good = tmp_path / "good.yaml"
    good.write_text(yaml.safe_dump(base | {"providers": ex["providers"]}))
    r = provider_check.check(good)
    assert r["valid"] and {p["id"]: p["status"] for p in r["providers"]} == {"searxng-main": "USABLE",
                                                                             "brave": "DISABLED"}
    for providers in ([{"id": "x", "kind": "searxng", "endpoint": "http://localhost:8080"}],
                      [{"id": "x", "kind": "searxng", "endpoint": "https://a.example.org"},
                       {"id": "x", "kind": "searxng", "endpoint": "https://b.example.org"}],
                      [{"id": "x", "kind": "google", "endpoint": "https://a.example.org"}]):
        bad = tmp_path / "bad.yaml"
        bad.write_text(yaml.safe_dump(base | {"providers": providers}))
        assert not provider_check.check(bad)["valid"], providers
    brave = tmp_path / "brave.yaml"
    brave.write_text(yaml.safe_dump(base | {"providers": [{"id": "brave-x", "kind": "brave",
                                                           "endpoint": "https://api.search.brave.com/x",
                                                           "credential_ref": "cred://stage02/search/brave"}]}))
    assert provider_check.check(brave)["providers"][0]["status"] == "BLOCKED"
