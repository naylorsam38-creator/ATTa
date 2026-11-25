"""Stage 04 API wiring for research (§28, §33) — one call, no edit of shared files required by this sidecar.

    from backend.research.wiring import wire_research_api
    app = create_app(...)
    wire_research_api(app)          # Stage 04 integrator adds this one line (e.g. in create_app or the launcher)

* mounts the typed research router under /v1 (idempotent: a second call does nothing);
* resolves the ResearchService lazily from the ONE controller built by the API lifespan
  (app.state.controller_service) — no second controller, registry or startup path;
* every route still evaluates the central policy engine: DENIED until Stage 02 grants the research.* operations.
Optional `service` lets tests or Stage 03 inject a prepared ResearchService (e.g. with a hub)."""
from __future__ import annotations

import threading

from backend.contracts import ApiError, ErrorCode

from .api import build_research_router

_lock = threading.Lock()


def wire_research_api(app, *, service=None, prefix: str = "/v1", service_kwargs: dict | None = None) -> bool:
    """Mount the research API on `app`. Returns True if mounted now, False if it was already mounted."""
    with _lock:
        if getattr(app.state, "research_wired", False):
            return False
        app.state.research_wired = True
        app.state.research_service = service
        app.state.research_service_kwargs = dict(service_kwargs or {})

    def get_service(request):
        st = request.app.state
        if getattr(st, "research_service", None) is not None:
            return st.research_service
        with _lock:
            if st.research_service is None:
                ctl = getattr(st, "controller_service", None)
                if ctl is None or getattr(ctl, "controller", None) is None:
                    raise ApiError(ErrorCode.NOT_READY, "research unavailable: the Stage 01A controller is not running")
                from . import build_research_service
                st.research_service = build_research_service(ctl, st.settings, **st.research_service_kwargs)
        return st.research_service

    app.include_router(build_research_router(get_service), prefix=prefix)
    return True
