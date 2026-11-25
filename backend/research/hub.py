"""Stage 03 model-integration contract for research (§26, §30) — controlled integration point.

Stage 03 owns inference. When it exists it supplies an object implementing ResearchHub (through the
model-independent Hub interface, never a second model server). Everything a hub returns is DATA and is validated
here before use; a hub cannot mint citation ids, choose tools, raise budgets or mark anything verified:

  plan_subquestions   -> list of strings, bounded by the contract's max_subquestions, length-checked
  formulate_query     -> one query string (<= 400 chars) for attempt 0/1 only; anything else is ignored
  build_claims        -> ClaimInput dicts; builder label is FORCED to 'hub:<name>'; citations still go through
                         claims.bind_claims (must resolve to stored evidence of this research)
  synthesise          -> {"text": ..., "citation_ids": [...]}: accepted only if every cited id is a VERIFIED
                         citation of this research and every [cit_...] marker in the text is one of them

Until Stage 03 is integrated the service runs with no hub: decomposition is deterministic, claim construction and
synthesis are BLOCKED (never faked)."""
from __future__ import annotations

import re
from typing import Protocol, runtime_checkable

from .contracts import ClaimInput, ResearchContract
from .errors import ResearchError, ResearchErrorCode

MAX_SUBQUESTION_CHARS = 500
MAX_QUERY_CHARS = 400          # same bound as planner.MAX_QUERY_CHARS
MAX_SYNTHESIS_CHARS = 20000
CITE_MARK = re.compile(r"\[(cit_[0-9A-HJKMNP-TV-Z]{26})\]")


@runtime_checkable
class ResearchHub(Protocol):
    name: str

    def available(self) -> bool: ...
    def plan_subquestions(self, contract: ResearchContract) -> list[str]: ...
    def formulate_query(self, contract: ResearchContract, subquestion: str, attempt: int) -> str | None: ...
    def build_claims(self, contract: ResearchContract, evidence_packet: dict) -> list[dict]: ...
    def synthesise(self, contract: ResearchContract, material: dict) -> dict: ...


def _blocked(what: str) -> ResearchError:
    return ResearchError(ResearchErrorCode.BLOCKED_DEPENDENCY, f"{what} needs the Stage 03 model interface (Hub), "
                         "which is not integrated", {"dependency": "S03"})


def hub_available(hub) -> bool:
    try:
        return hub is not None and bool(hub.available())
    except Exception:  # noqa: BLE001 — an erroring hub is unavailable, never trusted
        return False


class HubPlanner:
    """Model-driven decomposition/query formulation, validated and bounded. BLOCKED without a hub."""

    def __init__(self, hub: ResearchHub | None = None):
        self.hub = hub
        self.name = f"hub-planner:{getattr(hub, 'name', 'none')}"

    def decompose(self, contract: ResearchContract) -> list[str]:
        if contract.subquestions:                     # stored decomposition is authoritative (stable on resume)
            return list(contract.subquestions)[:contract.execution_budget.max_subquestions]
        if not hub_available(self.hub):
            raise _blocked("model-driven decomposition")
        raw = self.hub.plan_subquestions(contract)
        if not isinstance(raw, list):
            raise ResearchError(ResearchErrorCode.VERIFICATION_FAILED, "hub decomposition is not a list")
        subs = [s.strip()[:MAX_SUBQUESTION_CHARS] for s in raw if isinstance(s, str) and len(s.strip()) >= 3]
        subs = list(dict.fromkeys(subs))[:contract.execution_budget.max_subquestions]
        return subs or [contract.question]

    def query_for(self, contract: ResearchContract, subquestion: str, attempt: int) -> str | None:
        if attempt not in (0, 1):
            return None                               # bounded: never more than one reformulation
        if not hub_available(self.hub):
            raise _blocked("model-driven query formulation")
        q = self.hub.formulate_query(contract, subquestion, attempt)
        if not isinstance(q, str) or not q.strip():
            return None
        return " ".join(q.split())[:MAX_QUERY_CHARS]


class HubClaimBuilder:
    """Claims proposed by the Stage 03 hub. BLOCKED until a hub is available; never fabricates claims."""

    def __init__(self, hub: ResearchHub | None = None):
        self.hub = hub
        self.name = f"hub:{getattr(hub, 'name', 'none')}"

    def available(self) -> bool:
        return hub_available(self.hub)

    def build(self, contract: ResearchContract, evidence_packet: dict) -> list[ClaimInput]:
        if not self.available():
            raise _blocked("claim construction and natural-language synthesis")
        raw = self.hub.build_claims(contract, evidence_packet)
        if not isinstance(raw, list):
            raise ResearchError(ResearchErrorCode.VERIFICATION_FAILED, "hub claims are not a list")
        out = []
        for item in raw[:200]:
            if not isinstance(item, dict):
                continue
            try:
                out.append(ClaimInput.model_validate({**item, "builder": self.name}))   # label cannot be spoofed
            except Exception:  # noqa: BLE001 — malformed proposals are dropped, the rest are still validated
                continue
        return out


def validate_synthesis(raw, verified_citation_ids: set[str], *, require_citations: bool) -> tuple[dict | None, list[str]]:
    """Return (accepted synthesis, problems). A synthesis may only rest on VERIFIED citations of this research."""
    problems = []
    if not isinstance(raw, dict) or not isinstance(raw.get("text"), str):
        return None, ["synthesis must be an object with a 'text' string"]
    text = raw["text"].strip()
    if not text or len(text) > MAX_SYNTHESIS_CHARS:
        problems.append(f"synthesis text must be 1..{MAX_SYNTHESIS_CHARS} characters")
    declared = raw.get("citation_ids") or []
    if not isinstance(declared, list) or not all(isinstance(x, str) for x in declared):
        problems.append("citation_ids must be a list of strings")
        declared = []
    marked = set(CITE_MARK.findall(text))
    unknown = sorted((set(declared) | marked) - verified_citation_ids)
    if unknown:
        problems.append(f"synthesis cites ids that are not verified citations of this research: {unknown[:10]}")
    if set(declared) != marked:
        problems.append("citation_ids must equal the [cit_...] markers used in the text")
    if require_citations and verified_citation_ids and not marked:
        problems.append("synthesis makes material statements without citing verified evidence")
    if problems:
        return None, problems
    return {"text": text, "citation_ids": sorted(marked)}, []
