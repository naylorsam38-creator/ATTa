"""Typed /v1 research API contracts (§28, §33) — for Stage 04 (UI) and Stage 02 (authentication) integration.

Every route is evaluated by the CENTRAL policy engine with the request's authenticated principal (the existing
authenticator), exactly like backend/api/routes.py. Under Stage 01A that policy DENIES every protected operation, so
none of these operations is reachable until Stage 02 grants them to authenticated principals — nothing dangerous is
exposed unauthenticated. Mount with:  app.include_router(build_research_router(), prefix="/v1")
(not mounted by this sidecar: backend/api/app.py is a shared file — see STAGE_11_MERGE_MANIFEST.md)."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, Depends, Query, Request
from pydantic import BaseModel, ConfigDict, Field

from backend.authentication import get_authenticator
from backend.contracts import ApiError, ErrorCode, ErrorEnvelope
from backend.contracts import RequestContext
from backend.policy_engine import PolicyRequest

from .contracts import (CitationRequirements, DomainRestrictions, Freshness, OutputRequirements, ResearchMode,
                        ResearchStatus, SourceRequirements, TimeRestrictions, VerificationStatus)

OPERATIONS = {
    "create": "research.create", "get": "research.read", "list": "research.list", "start": "research.start",
    "cancel": "research.cancel", "progress": "research.read", "queries": "research.read", "sources": "research.read",
    "source": "research.read", "claims": "research.read", "citations": "research.read",
    "contradictions": "research.read", "checkpoint": "research.read", "result": "research.read",
    "events": "research.read",
}


class _M(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CreateResearchRequest(_M):
    question: str = Field(min_length=3, max_length=4000)
    research_type: ResearchMode
    scope: str = Field("", max_length=2000)
    subquestions: list[str] = Field(default_factory=list, max_length=40)
    claims_to_verify: list[str] = Field(default_factory=list, max_length=20)
    required_freshness: Freshness | None = None
    source_requirements: SourceRequirements | None = None
    excluded_sources: list[str] = Field(default_factory=list, max_length=200)
    domain_restrictions: DomainRestrictions | None = None
    time_restrictions: TimeRestrictions | None = None
    citation_requirements: CitationRequirements | None = None
    output_requirements: OutputRequirements | None = None
    budget: dict[str, int] | None = Field(None, description="requested limits; each must be <= the mode ceiling")
    follow_links: bool = False
    provider_ids: list[str] = Field(default_factory=list)


class ResearchSummary(_M):
    research_id: str
    mode: ResearchMode
    question: str
    status: ResearchStatus
    verification_status: VerificationStatus
    created_at: str
    updated_at: str


class ResearchView(_M):
    research_id: str
    status: ResearchStatus
    verification_status: VerificationStatus
    contract: dict[str, Any]
    contract_versions: list[dict[str, Any]]
    approval_required: dict[str, Any] | None


class StartRequest(_M):
    """Until Stage 02: an operator Ed25519 authorisation of the research objective (Stage 01A). Stage 02 replaces
    it with an authenticated human approval bound to principal, tenant and operation."""
    operator_authorization: dict[str, Any] | None = None
    resume: bool = False


class CancelRequest(_M):
    reason: str = Field("cancelled by user", max_length=500)


class Progress(_M):
    research_id: str
    status: ResearchStatus
    phase: str
    batch: int
    verification_status: VerificationStatus
    cancel_requested: bool
    error: dict[str, Any] | None
    budgets: dict[str, dict[str, float]]
    tasks: list[dict[str, Any]]
    active_subtasks: int = Field(le=4)
    counts: dict[str, int]
    latest_checkpoint: dict[str, Any] | None
    updated_at: str


class Page(_M):
    items: list[dict[str, Any]]
    next_cursor: str | None = None


def build_research_router(get_service=None) -> APIRouter:
    """get_service(request) -> ResearchService. Defaults to request.app.state.research_service."""
    router = APIRouter(tags=["research"])
    authenticator = get_authenticator()
    svc_of = get_service or (lambda request: getattr(request.app.state, "research_service", None))

    def authorize(operation: str):
        def dep(request: Request) -> RequestContext:
            principal = authenticator.authenticate(request.headers)
            policy = request.app.state.policy
            decision = policy.evaluate(PolicyRequest(principal=principal, operation=operation,
                                                     resource=request.path_params.get("research_id")))
            if not decision.allowed:
                raise ApiError(ErrorCode.PERMISSION_DENIED, decision.reason,
                               details={"operation": operation, "resource": request.path_params.get("research_id")})
            return RequestContext(request_id=getattr(request.state, "request_id", "req"),
                                  correlation_id=getattr(request.state, "correlation_id", None), principal=principal,
                                  idempotency_key=request.headers.get("Idempotency-Key"))
        return dep

    def service(request: Request):
        s = svc_of(request)
        if s is None:
            raise ApiError(ErrorCode.NOT_READY, "research service not available")
        return s

    def page(items: list[dict], limit: int, cursor: str | None, key: str) -> Page:
        start = 0
        if cursor:
            ids = [str(i[key]) for i in items]
            start = ids.index(cursor) + 1 if cursor in ids else 0
        chunk = items[start:start + limit]
        nxt = str(chunk[-1][key]) if len(items) > start + limit and chunk else None
        return Page(items=chunk, next_cursor=nxt)

    responses = {403: {"model": ErrorEnvelope}, 404: {"model": ErrorEnvelope}, 503: {"model": ErrorEnvelope}}

    @router.post("/research", response_model=ResearchView, status_code=201, responses=responses)
    def create_research(request: Request, body: CreateResearchRequest = Body(...),
                        ctx: RequestContext = Depends(authorize(OPERATIONS["create"]))):
        s = service(request)
        extra = {k: getattr(body, k) for k in ("required_freshness", "source_requirements", "domain_restrictions",
                                               "time_restrictions", "citation_requirements", "output_requirements")
                 if getattr(body, k) is not None}
        c = s.create(body.question, body.research_type, scope=body.scope, subquestions=body.subquestions,
                     claims_to_verify=body.claims_to_verify, budget=body.budget, follow_links=body.follow_links,
                     provider_ids=body.provider_ids, excluded_sources=body.excluded_sources, **extra)
        return _view(s, c.research_id)

    @router.get("/research", response_model=Page, responses=responses)
    def list_research(request: Request, limit: int = Query(50, ge=1, le=200), cursor: str | None = None,
                      ctx: RequestContext = Depends(authorize(OPERATIONS["list"]))):
        rows = service(request).store.list_runs(limit=limit, after=cursor)
        return Page(items=[_j(r) for r in rows], next_cursor=rows[-1]["research_id"] if len(rows) == limit else None)

    @router.get("/research/{research_id}", response_model=ResearchView, responses=responses)
    def get_research(research_id: str, request: Request, ctx: RequestContext = Depends(authorize(OPERATIONS["get"]))):
        return _view(service(request), research_id)

    @router.post("/research/{research_id}/start", response_model=Progress, status_code=202, responses=responses)
    def start_research(research_id: str, request: Request, body: StartRequest = Body(...),
                       ctx: RequestContext = Depends(authorize(OPERATIONS["start"]))):
        raise ApiError(ErrorCode.PERMISSION_DENIED, "research execution through the API needs Stage 02 approvals")

    @router.post("/research/{research_id}/cancel", response_model=Progress, responses=responses)
    def cancel_research(research_id: str, request: Request, body: CancelRequest = Body(...),
                        ctx: RequestContext = Depends(authorize(OPERATIONS["cancel"]))):
        s = service(request)
        s.cancel(research_id, body.reason)
        return s.progress(research_id)

    @router.get("/research/{research_id}/progress", response_model=Progress, responses=responses)
    def get_progress(research_id: str, request: Request,
                     ctx: RequestContext = Depends(authorize(OPERATIONS["progress"]))):
        return service(request).progress(research_id)

    for name, key, loader in (("queries", "query_id", lambda s, r: s.store.queries(r)),
                              ("sources", "source_id", lambda s, r: s.store.sources(r)),
                              ("claims", "claim_id", lambda s, r: s.store.claims(r)),
                              ("citations", "citation_id", lambda s, r: s.store.citations(r)),
                              ("contradictions", "contradiction_id", lambda s, r: s.store.contradictions(r)),
                              ("events", "event_id", lambda s, r: s.store.events(r))):
        def make(name=name, key=key, loader=loader):
            @router.get(f"/research/{{research_id}}/{name}", response_model=Page, responses=responses,
                        name=f"list_research_{name}")
            def listing(research_id: str, request: Request, limit: int = Query(50, ge=1, le=200),
                        cursor: str | None = None, ctx: RequestContext = Depends(authorize(OPERATIONS[name]))):
                s = service(request)
                return page([_j(r) for r in loader(s, research_id)], limit, cursor, key)
        make()

    @router.get("/research/{research_id}/sources/{source_id}", responses=responses)
    def get_source(research_id: str, source_id: str, request: Request,
                   ctx: RequestContext = Depends(authorize(OPERATIONS["source"]))):
        s = service(request).store.source(source_id)
        if s is None or s["research_id"] != research_id:
            raise ApiError(ErrorCode.NOT_FOUND, "source not found")
        return _j(s)

    @router.get("/research/{research_id}/checkpoint", responses=responses)
    def get_checkpoint(research_id: str, request: Request,
                       ctx: RequestContext = Depends(authorize(OPERATIONS["checkpoint"]))):
        cp = service(request).store.latest_checkpoint(research_id)
        if cp is None:
            raise ApiError(ErrorCode.NOT_FOUND, "no checkpoint yet")
        return _j(cp)

    @router.get("/research/{research_id}/result", responses=responses)
    def get_result(research_id: str, request: Request, ctx: RequestContext = Depends(authorize(OPERATIONS["result"]))):
        r = service(request).result(research_id)
        if r is None:
            raise ApiError(ErrorCode.NOT_FOUND, "no result yet")
        return r

    return router


def _j(row: dict) -> dict:
    import json
    return json.loads(json.dumps(dict(row), default=str))


def _view(s, research_id: str) -> ResearchView:
    run = s.store.run(research_id)
    c = s.store.contract(research_id)
    approved = s.controller.objective(c.objective_id).approval_status == "approved"
    return ResearchView(research_id=research_id, status=run["status"], verification_status=run["verification_status"],
                        contract=c.model_dump(mode="json"),
                        contract_versions=[_j(v) for v in s.store.contract_versions(research_id)],
                        approval_required=None if approved else s.approval_payload(research_id))


__all__ = ["build_research_router", "CreateResearchRequest", "ResearchView", "Progress", "Page", "StartRequest",
           "CancelRequest", "ResearchSummary", "OPERATIONS"]
