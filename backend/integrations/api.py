"""Stage 12 /v1 routes (contracts for Stage 04). Every route is evaluated by the central policy engine, which DENIES
all protected operations until Stage 02 delivers authentication, roles and approvals — exactly like the existing
/v1 routes. No route lets a model, worker or integration install, enable, trust or approve anything."""
from __future__ import annotations

from typing import Callable

from fastapi import APIRouter, Depends, Query, Request

from backend.contracts import ApiError, ErrorCode, RequestContext

from .manager import IntegrationError
from .service import (AuthorizedRequest, DisableRequest, InstallRequest, IntegrationDetail, IntegrationService,
                      IntegrationSummary, ToolInfo, ValidateRequest)
from .types import IntegrationType

_STATUS = {"NOT_FOUND": ErrorCode.NOT_FOUND, "REFUSED": ErrorCode.PERMISSION_DENIED, "FAILED": ErrorCode.CONFLICT}


def _svc(request: Request) -> IntegrationService:
    svc = getattr(request.app.state, "integrations", None)
    if svc is None:
        raise ApiError(ErrorCode.CAPABILITY_UNAVAILABLE, "integration service unavailable")
    return svc


def _call(fn, *a, **k):
    try:
        return fn(*a, **k)
    except IntegrationError as exc:
        raise ApiError(_STATUS.get(exc.status, ErrorCode.CONFLICT), str(exc), details={"problems": exc.problems}) from exc


def build_integrations_router(authorize: Callable[[str], Callable]) -> APIRouter:
    r = APIRouter(tags=["integrations"])

    @r.get("/integrations", response_model=list[IntegrationSummary])
    def list_integrations(request: Request, type: IntegrationType | None = Query(None),
                          _: RequestContext = Depends(authorize("integration.list"))):
        return _call(_svc(request).list, type)

    @r.post("/integrations/discover")
    def discover(request: Request, _: RequestContext = Depends(authorize("integration.discover"))):
        return _call(_svc(request).discover)

    @r.post("/integrations/validate")
    def validate(body: ValidateRequest, request: Request,
                 _: RequestContext = Depends(authorize("integration.validate"))):
        return _call(_svc(request).validate, body.source, body.trust_class)

    @r.post("/integrations/install")
    def install(body: InstallRequest, request: Request, _: RequestContext = Depends(authorize("integration.install"))):
        auth = body.authorization.to_auth() if body.authorization else None
        return _call(_svc(request).install, body.source, auth, body.trust_class)

    @r.get("/integrations/{integration_id}", response_model=IntegrationDetail)
    def get_integration(integration_id: str, request: Request,
                        _: RequestContext = Depends(authorize("integration.get"))):
        return _call(_svc(request).get, integration_id)

    @r.post("/integrations/{integration_id}/enable")
    def enable(integration_id: str, body: AuthorizedRequest, request: Request,
               _: RequestContext = Depends(authorize("integration.enable"))):
        return _call(_svc(request).enable, integration_id, body.authorization.to_auth() if body.authorization else None)

    @r.post("/integrations/{integration_id}/disable")
    def disable(integration_id: str, body: DisableRequest, request: Request,
                ctx: RequestContext = Depends(authorize("integration.disable"))):
        return _call(_svc(request).disable, integration_id, ctx.principal.id, body.reason)

    @r.post("/integrations/{integration_id}/upgrade")
    def upgrade(integration_id: str, body: InstallRequest, request: Request,
                _: RequestContext = Depends(authorize("integration.upgrade"))):
        return _call(_svc(request).upgrade, body.source, body.authorization.to_auth() if body.authorization else None,
                     body.trust_class)

    @r.delete("/integrations/{integration_id}")
    def remove(integration_id: str, request: Request, ctx: RequestContext = Depends(authorize("integration.remove"))):
        return _call(_svc(request).remove, integration_id, ctx.principal.id)

    @r.get("/integrations/{integration_id}/health")
    def health(integration_id: str, request: Request, _: RequestContext = Depends(authorize("integration.health"))):
        return _call(_svc(request).health, integration_id)

    @r.get("/integrations/{integration_id}/configuration-schema")
    def configuration_schema(integration_id: str, request: Request,
                             _: RequestContext = Depends(authorize("integration.get"))):
        return _call(_svc(request).configuration_schema, integration_id)

    @r.get("/integrations/{integration_id}/capabilities")
    def capabilities(integration_id: str, request: Request, _: RequestContext = Depends(authorize("integration.get"))):
        return _call(_svc(request).capabilities, integration_id)

    @r.get("/integrations/{integration_id}/tools", response_model=list[ToolInfo])
    def tools(integration_id: str, request: Request, _: RequestContext = Depends(authorize("integration.get"))):
        return _call(_svc(request).tools, integration_id)

    @r.get("/integrations/{integration_id}/permissions")
    def permissions(integration_id: str, request: Request, _: RequestContext = Depends(authorize("integration.get"))):
        return _call(_svc(request).permissions, integration_id)

    @r.get("/integrations/{integration_id}/events")
    def events(integration_id: str, request: Request, _: RequestContext = Depends(authorize("integration.audit"))):
        return _call(_svc(request).events, integration_id)

    @r.get("/mcp/servers")
    def mcp_servers(request: Request, _: RequestContext = Depends(authorize("integration.list"))):
        return _call(_svc(request).mcp_servers)

    @r.get("/mcp/servers/{integration_id}/capabilities")
    def mcp_caps(integration_id: str, request: Request, _: RequestContext = Depends(authorize("integration.get"))):
        return _call(_svc(request).mcp_capabilities, integration_id)

    @r.get("/skills")
    def skills(request: Request, _: RequestContext = Depends(authorize("integration.list"))):
        return _call(_svc(request).skills)

    @r.get("/skills/scripts")
    def scripts(request: Request, _: RequestContext = Depends(authorize("integration.list"))):
        return _call(_svc(request).list_scripts)

    @r.get("/plugins")
    def plugins(request: Request, _: RequestContext = Depends(authorize("integration.list"))):
        return _call(_svc(request).plugins)

    return r


def mount_integrations(app, authorize: Callable[[str], Callable]) -> bool:
    """Mount the Stage 12 routes once (idempotent): Stage 04 may call this too without duplicating routes."""
    if getattr(app.state, "stage12_router_mounted", False):
        return False
    app.include_router(build_integrations_router(authorize), prefix="/v1")
    app.state.stage12_router_mounted = True
    return True
