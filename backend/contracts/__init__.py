"""Common backend contracts shared by every backend component.

One definition per concept: identifiers, UTC time, request context, errors, pagination, idempotency,
correlation, logging, health, capability availability, task states, execution events and verification evidence.
"""
from .identifiers import new_id, parse_id, is_valid_id, ID_PREFIXES
from .timeutil import utc_now, to_utc, iso_utc
from .context import Principal, PrincipalKind, TenantContext, RequestContext
from .errors import ErrorCode, ApiError, ErrorBody, ErrorEnvelope
from .pagination import PageRequest, Page, paginate
from .idempotency import IdempotencyStore, InMemoryIdempotencyStore, IdempotencyConflict, request_fingerprint
from .correlation import CORRELATION_HEADER, get_correlation_id, set_correlation_id, new_correlation_id
from .logging import configure_logging, get_logger
from .health import HealthStatus, DependencyHealth, HealthReport, ReadinessReport
from .capability import RiskClass, AvailabilityState, CapabilityAvailability
from .tasks import TaskState, TERMINAL_STATES, ALLOWED_TRANSITIONS, InvalidTransition, assert_transition
from .events import ExecutionEvent, EventType
from .evidence import VerificationEvidence, EvidenceOutcome

__all__ = [n for n in dir() if not n.startswith("_")]
