"""Typed Stage 11 research contracts and records (§6, §7, §13, §15, §16, §17, §19, §20, §26).

Strict pydantic models (unknown fields forbidden). A ResearchContract is DATA owned by the research service: it is
versioned (every change is a new version with a new SHA-256 hash, stored append-only) and nothing a web page or a
worker produces can change it."""
from __future__ import annotations

from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from backend.contracts import canonical_hash

RID = r"^rsr_[0-9A-HJKMNP-TV-Z]{26}$"
DOMAIN = r"^(\*\.)?[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?(\.[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?)+$"


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ResearchMode(str, Enum):
    QUICK_SEARCH = "QUICK_SEARCH"
    STANDARD_RESEARCH = "STANDARD_RESEARCH"
    DEEP_RESEARCH = "DEEP_RESEARCH"
    SOURCE_COMPARISON = "SOURCE_COMPARISON"
    FACT_VERIFICATION = "FACT_VERIFICATION"


class ResearchStatus(str, Enum):
    CREATED = "CREATED"                      # contract stored; objective awaits operator authorisation (Stage 01A)
    RUNNING = "RUNNING"
    PAUSED = "PAUSED"                        # checkpointed, resumable (batch limit reached / restart)
    CANCELLING = "CANCELLING"
    CANCELLED = "CANCELLED"
    COMPLETED = "COMPLETED"                  # verified, no limitation
    COMPLETED_WITH_LIMITATIONS = "COMPLETED_WITH_LIMITATIONS"   # verified, limitations listed in the result
    FAILED = "FAILED"
    BLOCKED = "BLOCKED"                      # cannot proceed: error_code says why (offline, provider, dependency)


TERMINAL_STATUSES = frozenset({ResearchStatus.CANCELLED, ResearchStatus.COMPLETED,
                               ResearchStatus.COMPLETED_WITH_LIMITATIONS, ResearchStatus.FAILED,
                               ResearchStatus.BLOCKED})


class VerificationStatus(str, Enum):
    NOT_VERIFIED = "NOT_VERIFIED"
    VERIFIED = "VERIFIED"
    VERIFICATION_FAILED = "VERIFICATION_FAILED"


class NetworkMode(str, Enum):
    """Stage 01B owns detection of the deployment mode; Stage 11 only consumes it (network.py)."""
    ONLINE = "ONLINE"
    OFFLINE = "OFFLINE"
    AIR_GAPPED = "AIR_GAPPED"


class Freshness(_Strict):
    requires_current: bool = False                 # "current information" request
    max_age_days: int | None = Field(None, ge=0, le=36500)
    reference_time: str | None = None               # ISO time the age is measured from (default: research start)


class SourceRequirements(_Strict):
    min_sources: int = Field(1, ge=0, le=100)
    min_independent_sources: int = Field(1, ge=0, le=100)
    prefer_primary: bool = False
    require_publication_date: bool = False
    allowed_content_types: list[str] = Field(default_factory=list)
    seed_urls: list[str] = Field(default_factory=list, max_length=20)        # SOURCE_COMPARISON / known sources
    local_sources: list[str] = Field(default_factory=list, max_length=50)    # authorised workspace-relative files

    @field_validator("local_sources")
    @classmethod
    def _local(cls, v):
        for p in v:
            if p.startswith(("/", "\\", "~")) or ":" in p or ".." in p.replace("\\", "/").split("/"):
                raise ValueError(f"local sources must be workspace-relative without '..': {p!r}")
        return v


class DomainRestrictions(_Strict):
    allowed_domains: list[str] = Field(default_factory=list)       # empty = any public domain
    excluded_domains: list[str] = Field(default_factory=list)

    @field_validator("allowed_domains", "excluded_domains")
    @classmethod
    def _domains(cls, v):
        import re
        out = []
        for d in v:
            d = d.strip().lower().rstrip(".")
            if not re.match(DOMAIN, d):
                raise ValueError(f"invalid domain {d!r}")
            out.append(d)
        return out


class TimeRestrictions(_Strict):
    published_after: str | None = None
    published_before: str | None = None
    provider_time_range: Literal["day", "week", "month", "year"] | None = None


class CitationRequirements(_Strict):
    require_citations: bool = True                 # every material claim must cite stored evidence
    min_citations_per_claim: int = Field(1, ge=0, le=10)
    require_span: bool = True                      # citations must point at an exact span of the stored text


class OutputRequirements(_Strict):
    include_contradictions: bool = True
    include_unresolved_questions: bool = True
    max_findings: int = Field(20, ge=1, le=200)
    format: Literal["structured"] = "structured"   # natural-language synthesis: Stage 03 (BLOCKED)


class ExecutionBudget(_Strict):
    """Research-level limits. Each is enforced atomically in research.budgets; they can never be raised by the
    research worker, and only by a new contract version within the trusted ceilings (configuration/research.yaml)."""
    max_queries: int = Field(ge=0)
    max_sources: int = Field(ge=0)
    max_fetches: int = Field(ge=0)
    max_depth: int = Field(ge=0)
    max_subquestions: int = Field(ge=1)
    max_batches: int = Field(ge=1)
    time_limit_s: int = Field(ge=1)
    token_limit: int = Field(ge=0)
    context_tokens_per_task: int = Field(ge=256)
    network_requests: int = Field(ge=0)
    max_retries: int = Field(ge=0)
    max_evidence_kb: int = Field(65536, ge=1)       # stored snapshot bytes + extracted text (KiB)
    max_model_requests: int = Field(50, ge=0)       # Stage 03 hub calls (planning, queries, claims, synthesis)


class NetworkPolicy(_Strict):
    mode: NetworkMode                              # effective mode when the contract version was made
    allow_external: bool                           # False => only authorised local/project sources
    follow_links: bool = False
    provider_ids: list[str] = Field(default_factory=list)   # explicitly configured providers to use


class ResearchContract(_Strict):
    research_id: str = Field(pattern=RID)
    objective_id: str | None = None                # Stage 01A objective that executes this research
    task_id: str | None = None                     # parent task (when research runs as part of a larger task)
    question: str = Field(min_length=3, max_length=4000)
    research_type: ResearchMode
    scope: str = Field("", max_length=2000)
    subquestions: list[str] = Field(default_factory=list, max_length=40)
    claims_to_verify: list[str] = Field(default_factory=list, max_length=20)   # FACT_VERIFICATION
    required_freshness: Freshness = Field(default_factory=Freshness)
    source_requirements: SourceRequirements = Field(default_factory=SourceRequirements)
    excluded_sources: list[str] = Field(default_factory=list, max_length=200)  # exact URLs never used
    domain_restrictions: DomainRestrictions = Field(default_factory=DomainRestrictions)
    time_restrictions: TimeRestrictions = Field(default_factory=TimeRestrictions)
    maximum_queries: int = Field(ge=0)
    maximum_sources: int = Field(ge=0)
    maximum_fetches: int = Field(ge=0)
    maximum_depth: int = Field(ge=0)
    citation_requirements: CitationRequirements = Field(default_factory=CitationRequirements)
    output_requirements: OutputRequirements = Field(default_factory=OutputRequirements)
    execution_budget: ExecutionBudget
    network_policy: NetworkPolicy
    status: ResearchStatus = ResearchStatus.CREATED
    contract_version: int = Field(1, ge=1)
    created_at: str
    updated_at: str

    @model_validator(mode="after")
    def _consistent(self):
        b = self.execution_budget
        if (self.maximum_queries, self.maximum_sources, self.maximum_fetches, self.maximum_depth) != \
                (b.max_queries, b.max_sources, b.max_fetches, b.max_depth):
            raise ValueError("maximum_* fields must equal the execution budget")
        if self.research_type is ResearchMode.FACT_VERIFICATION and not self.claims_to_verify:
            raise ValueError("FACT_VERIFICATION needs claims_to_verify")
        if self.research_type is ResearchMode.SOURCE_COMPARISON and \
                len(self.source_requirements.seed_urls) + len(self.source_requirements.local_sources) < 2:
            raise ValueError("SOURCE_COMPARISON needs at least two seed URLs or local sources")
        if len(self.subquestions) > b.max_subquestions:
            raise ValueError(f"{len(self.subquestions)} subquestions exceed max_subquestions={b.max_subquestions}")
        return self

    def body(self) -> dict:
        return self.model_dump(mode="json")

    def hash(self) -> str:
        return canonical_hash(self.body())


# ------------------------------------------------------------------------------------------------ records
class SourceKind(str, Enum):
    WEB = "web"
    LOCAL = "local"            # authorised workspace/project file
    PROJECT = "project"        # Stage 07 project knowledge (interface only)


class SourceQuality(_Strict):
    """Explicit, inspectable factors — NOT a truth score. Unknown stays None; nothing is inferred silently."""
    primary: bool | None = None                    # stated by the planner/claim builder, or None (unknown)
    official: bool | None = None                   # domain matches a configured official-domain list
    publication_identified: bool                   # publisher or site name stated
    author_identified: bool
    publication_date_known: bool
    has_citations: bool                            # outbound links present in the extracted text
    independence_group: str                        # sources in the same group are NOT independent corroboration
    retrieval_complete: bool                       # full body retrieved and extracted
    notes: list[str] = Field(default_factory=list)


class Span(_Strict):
    start: int = Field(ge=0)
    end: int = Field(ge=1)
    quote_sha256: str = Field(min_length=64, max_length=64)

    @model_validator(mode="after")
    def _order(self):
        if self.end <= self.start:
            raise ValueError("span end must be after start")
        return self


class ClaimInput(_Strict):
    """What a claim builder (Stage 03 model, or an explicit fixture) proposes. Citations reference STORED evidence:
    either an evidence id the service issued, or an exact quote that must occur in a stored source of this
    research. Arbitrary citation identifiers are rejected."""
    text: str = Field(min_length=3, max_length=2000)
    material: bool = True
    subquestion_index: int | None = None
    subject: str | None = Field(None, max_length=200)      # structured form enables deterministic contradiction checks
    attribute: str | None = Field(None, max_length=200)
    value: str | None = Field(None, max_length=500)
    as_of: str | None = None
    scope: str | None = Field(None, max_length=300)
    support: list[dict[str, Any]] = Field(default_factory=list)       # [{"evidence_id"} | {"source_id","quote"}]
    contradicts: list[dict[str, Any]] = Field(default_factory=list)   # same shape: evidence AGAINST the claim
    confidence: Literal["low", "medium", "high"] | None = None
    builder: str = Field(min_length=1)                     # who proposed it: "explicit-test", "hub:<model>", ...


class ResearchResult(_Strict):
    research_id: str
    objective: str
    scope: str
    research_type: ResearchMode
    contract_version: int
    contract_hash: str
    summary: dict
    findings: list[dict]
    claims: list[dict]
    citations: list[dict]
    sources: list[dict]
    contradictions: list[dict]
    limitations: list[dict]
    unresolved_questions: list[str]
    research_status: ResearchStatus
    verification_status: VerificationStatus
    verification: dict
    error: dict | None = None
    generated_at: str
    synthesis: dict
