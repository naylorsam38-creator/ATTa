"""Protected /v1 routes. They exist so the contract is fixed, but every call is evaluated by the central policy
engine — which denies all protected operations until Stage 02 delivers authentication and authorisation."""
from __future__ import annotations

from typing import Any, Callable

from fastapi import APIRouter, Body, Depends, Query, Request

from backend.capability_registry import CapabilityRegistry
from backend.contracts import ApiError, ErrorCode, PageRequest, RequestContext, paginate


def build_router(authorize: Callable[[str], Callable]) -> APIRouter:
    router = APIRouter()

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
        # Unreachable before Stage 02 (the policy dependency denies every caller). Even then there is no direct
        # route: executions are created only as controller tasks and run only through the execution gateway.
        raise ApiError(ErrorCode.CAPABILITY_UNAVAILABLE, "direct capability execution is not offered; submit an "
                       "objective to the runtime controller (Stage 02 exposes it to authenticated users)")

    @router.post("/control/objectives", tags=["control"])
    def create_objective(ctx: RequestContext = Depends(authorize("control.objective.create"))):
        raise ApiError(ErrorCode.PERMISSION_DENIED, "not reachable: protected")

    @router.post("/control/tasks/{task_id}/proposals", tags=["control"])
    def submit_proposal(task_id: str, ctx: RequestContext = Depends(authorize("control.proposal.submit"))):
        raise ApiError(ErrorCode.PERMISSION_DENIED, "not reachable: protected")

    return router
