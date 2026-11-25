"""Stage 11 unit tests: research contracts, modes, ceilings, error model, provider/network interfaces (no DB)."""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from backend.research.config import load_research_config
from backend.research.contracts import (ExecutionBudget, NetworkMode, NetworkPolicy, ResearchContract, ResearchMode,
                                        SourceRequirements)
from backend.research.errors import ResearchErrorCode
from backend.research.ids import rid
from backend.research.network import ConfiguredNetworkMode, external_allowed
from backend.research.planner import DeterministicPlanner, HubPlanner
from backend.research.errors import ResearchError

CFG = load_research_config()


def contract(mode=ResearchMode.STANDARD_RESEARCH, **kw) -> ResearchContract:
    eb = CFG.ceiling(mode)
    base = dict(research_id=rid("rsr"), question="What happened to the bridge?", research_type=mode,
                maximum_queries=eb.max_queries, maximum_sources=eb.max_sources, maximum_fetches=eb.max_fetches,
                maximum_depth=eb.max_depth, execution_budget=eb,
                network_policy=NetworkPolicy(mode=NetworkMode.ONLINE, allow_external=True),
                created_at="2026-11-25T00:00:00Z", updated_at="2026-11-25T00:00:00Z")
    base.update(kw)
    return ResearchContract.model_validate(base)


def test_contract_has_all_required_fields_and_hash_is_stable():
    c = contract()
    for f in ("research_id", "objective_id", "task_id", "question", "research_type", "scope", "required_freshness",
              "source_requirements", "excluded_sources", "domain_restrictions", "time_restrictions",
              "maximum_queries", "maximum_sources", "maximum_fetches", "maximum_depth", "citation_requirements",
              "output_requirements", "execution_budget", "network_policy", "status", "created_at", "updated_at",
              "contract_version"):
        assert f in type(c).model_fields, f
    assert c.hash() == contract(research_id=c.research_id).hash()
    v2 = ResearchContract.model_validate(c.model_dump(mode="json") | {"contract_version": 2})
    assert v2.hash() != c.hash()


def test_contract_rejects_unknown_fields_and_inconsistent_limits():
    with pytest.raises(ValidationError):
        contract(grant_tools=["019.001"])
    with pytest.raises(ValidationError):
        contract(maximum_queries=999)        # must equal the execution budget
    with pytest.raises(ValidationError):
        contract(question="")
    with pytest.raises(ValidationError):
        contract(subquestions=[f"q{i}" for i in range(9)])      # > max_subquestions (4) for STANDARD
    with pytest.raises(ValidationError):
        contract(mode=ResearchMode.FACT_VERIFICATION)           # needs claims_to_verify
    with pytest.raises(ValidationError):
        contract(mode=ResearchMode.SOURCE_COMPARISON)           # needs >= 2 sources
    with pytest.raises(ValidationError):
        SourceRequirements(local_sources=["../etc/passwd"])
    with pytest.raises(ValidationError):
        contract(domain_restrictions={"allowed_domains": ["not a domain"]})


def test_all_five_modes_have_bounded_trusted_ceilings():
    assert set(CFG.modes) == set(ResearchMode)
    q = CFG.ceiling(ResearchMode.QUICK_SEARCH)
    d = CFG.ceiling(ResearchMode.DEEP_RESEARCH)
    assert q.max_queries <= 2 and q.max_depth == 0 and q.max_subquestions == 1      # QUICK stays bounded
    assert d.max_subquestions > CFG.max_active_subtasks <= 4                         # deep = several Focus-4 batches
    for mode in ResearchMode:
        eb = CFG.ceiling(mode)
        assert eb.max_batches >= 1 and eb.time_limit_s > 0 and eb.max_depth <= 2


def test_focus4_ceiling_cannot_exceed_four():
    with pytest.raises(ValidationError):
        type(CFG).model_validate(CFG.model_dump() | {"max_active_subtasks": 5})


def test_error_model_is_distinct_codes():
    assert {c.value for c in ResearchErrorCode} == {
        "PROVIDER_UNAVAILABLE", "OFFLINE_UNAVAILABLE", "NETWORK_DENIED", "SOURCE_UNAVAILABLE", "SOURCE_TOO_LARGE",
        "UNSUPPORTED_CONTENT", "EXTRACTION_FAILED", "BUDGET_EXHAUSTED", "CANCELLED", "VERIFICATION_FAILED",
        "BLOCKED_DEPENDENCY"}


def test_network_mode_provider(monkeypatch):
    monkeypatch.setenv("ATTA_NETWORK_MODE", "AIR_GAPPED")
    assert ConfiguredNetworkMode(NetworkMode.ONLINE).current_mode() is NetworkMode.AIR_GAPPED
    monkeypatch.setenv("ATTA_NETWORK_MODE", "nonsense")
    assert ConfiguredNetworkMode(NetworkMode.ONLINE).current_mode() is NetworkMode.AIR_GAPPED   # fails closed
    monkeypatch.delenv("ATTA_NETWORK_MODE")
    assert ConfiguredNetworkMode(NetworkMode.OFFLINE).current_mode() is NetworkMode.OFFLINE
    assert external_allowed(NetworkMode.ONLINE) and not external_allowed(NetworkMode.OFFLINE)


def test_no_provider_configured_by_default():
    assert CFG.providers == []          # explicit configuration only; nothing hard-wired


def test_planner_is_bounded_and_hub_is_blocked():
    p = DeterministicPlanner()
    c = contract(mode=ResearchMode.DEEP_RESEARCH, subquestions=[f"sub question {i}" for i in range(12)])
    assert len(p.decompose(c)) == 12 and p.query_for(c, "sub question 1", 2) is None   # no unbounded reformulation
    assert HubPlanner().decompose(c) == list(c.subquestions)      # stored decomposition is authoritative
    with pytest.raises(ResearchError) as e:
        HubPlanner().decompose(contract(mode=ResearchMode.DEEP_RESEARCH))
    assert e.value.code is ResearchErrorCode.BLOCKED_DEPENDENCY


def test_budget_model_rejects_negative():
    with pytest.raises(ValidationError):
        ExecutionBudget(max_queries=-1, max_sources=1, max_fetches=1, max_depth=0, max_subquestions=1, max_batches=1,
                        time_limit_s=1, token_limit=1, context_tokens_per_task=256, network_requests=1, max_retries=0)
