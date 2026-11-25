"""Protected /v1 routes. They exist so the contract is fixed, but every call is evaluated by the central policy
engine — which denies all protected operations until Stage 02 delivers authentication and authorisation."""
from __future__ import annotations

from typing import Any, Callable

from fastapi import APIRouter, Body, Depends, Query, Request

from backend.capability_registry import CapabilityRegistry
from backend.contracts import (ApiError, ErrorCode, PageRequest, RequestContext, paginate, request_fingerprint)
from backend.contracts.idempotency import IdempotencyConflict, InMemoryIdempotencyStore


def build_router(authorize: Callable[[str], Callable]) -> APIRouter:
    router = APIRouter()
    idempotency = InMemoryIdempotencyStore()

    @router.get("/capabilities", tags=["capabilities"])
    def list_capabilities(request: Request, limit: int = Query(50, ge=1, le=200), cursor: str | None = None,
                          ctx: RequestContext = Depends(authorize("capability.list"))):
        reg: CapabilityRegistry = request.app.state.registry
        try:
            page = paginate([a.model_dump(mode="json") for a in reg.availability()],
                            PageRequest(limit=limit, cursor=cursor))
        except ValueError as exc:
            raise ApiError(ErrorCode.VALIDATION_FAILED, str(exc)) from exc
        return page.model_dump(mode="json")

    @router.post("/capabilities/{capability_id}/executions", tags=["capabilities"], status_code=200)
    def execute_capability(capability_id: str, request: Request, payload: dict[str, Any] = Body(default_factory=dict),
                           ctx: RequestContext = Depends(authorize("capability.execute"))):
        reg: CapabilityRegistry = request.app.state.registry
        key = ctx.idempotency_key
        fp = request_fingerprint("POST", request.url.path, payload)
        if key:
            try:
                hit = idempotency.lookup(ctx.principal.id, key, fp)
            except IdempotencyConflict as exc:
                raise ApiError(ErrorCode.IDEMPOTENCY_CONFLICT, str(exc)) from exc
            if hit:
                return hit.body
        result = reg.execute(capability_id, payload, ctx)
        body = {"capability_id": result.capability_id, "output": result.output, "verified": result.verified,
                "duration_ms": result.duration_ms}
        if key:
            idempotency.store(ctx.principal.id, key, fp, 200, body)
        return body

    return router
