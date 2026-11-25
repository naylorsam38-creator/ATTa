"""Stage 11 — web search and deep research (CAP-11, CAP-12), built on the Stage 01A runtime controller.

See documentation/stage11/RESEARCH_ARCHITECTURE.md. Nothing here is a second Hub, task engine, registry, policy
engine or approval system: research executes as Stage 01A tasks through the existing execution gateway."""
from .contracts import (NetworkMode, ResearchContract, ResearchMode, ResearchResult, ResearchStatus,
                        VerificationStatus)
from .errors import ResearchError, ResearchErrorCode

__all__ = ["NetworkMode", "ResearchContract", "ResearchMode", "ResearchResult", "ResearchStatus",
           "VerificationStatus", "ResearchError", "ResearchErrorCode", "build_research_service"]


def build_research_service(controller_service, settings=None, **kw):
    """Factory over the ONE controller built by backend.orchestration.service.build_controller_service."""
    from backend.database.session import Database
    from backend.settings import load_settings

    from .config import load_research_config
    from .service import ResearchService
    from .store import ResearchStore
    ctl = controller_service.controller
    if ctl is None:
        raise ResearchError(ResearchErrorCode.BLOCKED_DEPENDENCY, "the Stage 01A controller is not available: "
                            + "; ".join(controller_service.problems))
    return ResearchService(controller=ctl, store=ResearchStore(Database(settings or load_settings())),
                           config=load_research_config(), **kw)
