"""Bounded research planning (§7, §21, §30).

Decomposition is one level deep by construction: OBJECTIVE -> subquestions (<= max_subquestions). Subquestions are
processed in Focus-4 batches; after each batch the service checkpoints, identifies evidence gaps, and may issue ONE
reformulated query per gap subquestion (attempt 1) while budgets last. There is no recursion: a subquestion never
spawns subquestions, and the number of batches, queries, fetches and link-follow depth are all capped by the
contract and its trusted ceilings.

Model-driven planning (question decomposition, query formulation) is the Stage 03 Hub's job: HubPlanner is BLOCKED
until then. DeterministicPlanner uses only what the contract states (explicit subquestions, or the question itself)
and simple keyword reformulation — it does not pretend to be a model."""
from __future__ import annotations

from typing import Protocol, runtime_checkable

from .contracts import ResearchContract
from .errors import ResearchError, ResearchErrorCode
from .evidence import terms_of

MAX_QUERY_CHARS = 400


@runtime_checkable
class ResearchPlanner(Protocol):
    name: str

    def decompose(self, contract: ResearchContract) -> list[str]: ...
    def query_for(self, contract: ResearchContract, subquestion: str, attempt: int) -> str | None: ...


class DeterministicPlanner:
    name = "deterministic-planner/1"

    def decompose(self, contract: ResearchContract) -> list[str]:
        subs = list(contract.subquestions) or list(contract.claims_to_verify) or [contract.question]
        return subs[:contract.execution_budget.max_subquestions]

    def query_for(self, contract: ResearchContract, subquestion: str, attempt: int) -> str | None:
        if attempt == 0:
            return subquestion.strip()[:MAX_QUERY_CHARS]
        if attempt == 1:     # gap reformulation: keywords only, plus the stated scope
            kw = " ".join(terms_of(subquestion))
            scope = " ".join(terms_of(contract.scope))[:100]
            q = (kw + (" " + scope if scope else "")).strip()
            return q[:MAX_QUERY_CHARS] if q and q != subquestion.strip() else None
        return None


class HubPlanner:
    name = "hub-planner"

    def __init__(self, hub=None):
        self.hub = hub

    def decompose(self, contract):
        raise ResearchError(ResearchErrorCode.BLOCKED_DEPENDENCY, "model-driven decomposition needs the Stage 03 "
                            "Hub interface", {"dependency": "S03"})

    def query_for(self, contract, subquestion, attempt):
        raise ResearchError(ResearchErrorCode.BLOCKED_DEPENDENCY, "model-driven query formulation needs Stage 03",
                            {"dependency": "S03"})
