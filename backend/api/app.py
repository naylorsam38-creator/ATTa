"""ASGI application factory.  uvicorn backend.api.app:create_app --factory"""
from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from typing import Callable

from fastapi import Depends, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from backend import __version__
from backend.authentication import get_authenticator
from backend.capability_registry import CapabilityRegistry
from backend.contracts import (CORRELATION_HEADER, ApiError, DependencyHealth, ErrorCode, Principal, RequestContext,
                               configure_logging, get_logger, new_id, set_correlation_id)
from backend.contracts.health import HealthReport, HealthStatus, ReadinessReport
from backend.policy_engine import PolicyEngine, PolicyRequest, get_policy_engine
from backend.service_manager.probes import probe_service
from backend.settings import Settings, load_settings
from backend.settings.loader import resolve_path

from .routes import build_router

log = get_logger("atta.api")
SERVICE_NAME = "atta-api"


def default_dependency_probe(settings: Settings) -> Callable[[], list[DependencyHealth]]:
    items = [(n, s) for n, s in settings.services.items() if s.mode != "disabled" and n not in ("api", "controller")]

    def probe() -> list[DependencyHealth]:
        # Probes run concurrently so readiness latency is bounded by the slowest probe, not their sum.
        with ThreadPoolExecutor(max_workers=max(1, len(items))) as pool:
            return list(pool.map(lambda it: probe_service(it[0], it[1].essential, it[1].health, settings), items))
    return probe


def create_app(settings: Settings | None = None, *, policy: PolicyEngine | None = None,
               dependency_probe: Callable[[], list[DependencyHealth]] | None = None,
               registry: CapabilityRegistry | None = None) -> FastAPI:
    settings = settings or load_settings()
    configure_logging(settings.logging.level, settings.logging.format)
    policy = policy or get_policy_engine()
    probe = dependency_probe or default_dependency_probe(settings)
    authenticator = get_authenticator()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if app.state.registry is None:
            # One registry, one controller, live service health (correction 3) — built by the single factory.
            from backend.orchestration.service import build_controller_service
            svc = build_controller_service(settings, policy=policy)
            app.state.controller_service = svc
            app.state.registry = svc.registry
            # Stage 12: integrations attach to the same controller by composition; failure never blocks start.
            from backend.integrations.service import attach_to_controller_service
            app.state.integrations = attach_to_controller_service(svc, settings)
        log.info("api started", extra={"environment": settings.environment.value, "version": __version__})
        yield
        svc = getattr(app.state, "controller_service", None)
        if svc and svc.monitor:
            svc.monitor.stop()
        log.info("api stopping")

    dev = settings.environment.value in ("development", "test")
    app = FastAPI(title="ATTa API", version=__version__, lifespan=lifespan,
                  openapi_url="/v1/openapi.json" if dev else None, docs_url="/v1/docs" if dev else None,
                  redoc_url=None)
    app.state.settings, app.state.policy, app.state.registry = settings, policy, registry
    app.state.controller_service = None
    app.state.integrations = None
    app.state.dependency_probe = probe

    app.add_middleware(CORSMiddleware, allow_origins=settings.security.cors_origins, allow_credentials=False,
                       allow_methods=["GET", "POST", "PATCH", "DELETE"],
                       allow_headers=["Content-Type", "Idempotency-Key", CORRELATION_HEADER])

    @app.middleware("http")
    async def context_middleware(request: Request, call_next):
        cid = set_correlation_id(request.headers.get(CORRELATION_HEADER))
        request.state.correlation_id = cid
        request.state.request_id = new_id("req")
        t0 = time.perf_counter()
        response = await call_next(request)
        response.headers[CORRELATION_HEADER] = cid
        response.headers["X-Request-ID"] = request.state.request_id
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Cache-Control"] = "no-store"
        log.info("request", extra={"method": request.method, "path": request.url.path,
                                   "status": response.status_code,
                                   "duration_ms": round((time.perf_counter() - t0) * 1000, 2)})
        return response

    def _cid(request: Request) -> str | None:
        return getattr(request.state, "correlation_id", None)

    @app.exception_handler(ApiError)
    async def api_error(request: Request, exc: ApiError):
        return JSONResponse(exc.envelope(_cid(request)), status_code=exc.status)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError):
        details = [{"loc": list(e.get("loc", [])), "msg": e.get("msg")} for e in exc.errors()]
        return JSONResponse(ApiError(ErrorCode.VALIDATION_FAILED, "request validation failed", details)
                            .envelope(_cid(request)), status_code=422)

    @app.exception_handler(StarletteHTTPException)
    async def http_error(request: Request, exc: StarletteHTTPException):
        code = {404: ErrorCode.NOT_FOUND, 405: ErrorCode.VALIDATION_FAILED}.get(exc.status_code, ErrorCode.INTERNAL)
        return JSONResponse(ApiError(code, str(exc.detail), status=exc.status_code).envelope(_cid(request)),
                            status_code=exc.status_code)

    @app.exception_handler(Exception)
    async def unhandled(request: Request, exc: Exception):
        log.exception("unhandled error")
        return JSONResponse(ApiError(ErrorCode.INTERNAL, "internal error").envelope(_cid(request)), status_code=500)

    def request_context(request: Request) -> RequestContext:
        principal: Principal = authenticator.authenticate(request.headers)
        return RequestContext(request_id=request.state.request_id, correlation_id=request.state.correlation_id,
                              principal=principal, idempotency_key=request.headers.get("Idempotency-Key"))

    def authorize(operation: str):
        def dep(request: Request, ctx: RequestContext = Depends(request_context)) -> RequestContext:
            resource = request.path_params.get("capability_id")
            decision = policy.evaluate(PolicyRequest(principal=ctx.principal, operation=operation, resource=resource))
            if not decision.allowed:
                raise ApiError(ErrorCode.PERMISSION_DENIED, decision.reason,
                               details={"operation": operation, "resource": resource})
            return ctx
        return dep

    app.include_router(build_router(authorize), prefix="/v1")
    from backend.integrations.api import build_integrations_router   # Stage 12 contracts (policy-protected)
    app.include_router(build_integrations_router(authorize), prefix="/v1")

    @app.get("/v1/health/live", response_model=HealthReport, tags=["health"])
    def live(_: RequestContext = Depends(authorize("health.live"))):
        return HealthReport(status=HealthStatus.HEALTHY, service=SERVICE_NAME, version=__version__)

    @app.get("/v1/health/ready", response_model=ReadinessReport, tags=["health"],
             responses={503: {"model": ReadinessReport}})
    def ready(request: Request, _: RequestContext = Depends(authorize("health.ready"))):
        deps = request.app.state.dependency_probe()
        reg: CapabilityRegistry | None = request.app.state.registry
        caps = reg.availability() if reg else []
        if reg is None:
            deps.append(DependencyHealth(name="capability_registry", essential=True,
                                         status=HealthStatus.UNHEALTHY, detail="registry not initialised"))
        report = ReadinessReport.from_dependencies(settings.environment.value, deps, caps)
        return JSONResponse(report.model_dump(mode="json"), status_code=200 if report.ready else 503)

    @app.get("/v1/health/controller", tags=["health"])
    def controller_health(request: Request, _: RequestContext = Depends(authorize("health.controller"))):
        """Runtime controller readiness (Stage 01A §23). 503 = autonomous execution is unavailable."""
        svc = request.app.state.controller_service
        if svc is None:
            body = {"ready": False, "components": {"controller": {"ok": False, "detail": "not started"}},
                    "active_tasks": None, "blocked_tasks": None}
        else:
            body = svc.health()
        return JSONResponse(body, status_code=200 if body["ready"] else 503)

    @app.get("/v1/system/version", tags=["system"])
    def version(_: RequestContext = Depends(authorize("system.version"))):
        return {"service": SERVICE_NAME, "version": __version__, "api_version": "v1", "stage": "S01A"}

    return app
