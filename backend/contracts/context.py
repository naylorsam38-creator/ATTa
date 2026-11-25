"""User and tenant context attached to every request and every capability execution."""
from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, ConfigDict, Field


class PrincipalKind(str, Enum):
    ANONYMOUS = "anonymous"
    USER = "user"
    SERVICE = "service"        # an internal platform service
    AGENT = "agent"            # an AI agent acting on behalf of a user
    CAPABILITY = "capability"  # a capability handler
    WORKER = "worker"          # a background worker


class Principal(BaseModel):
    model_config = ConfigDict(frozen=True)
    id: str
    kind: PrincipalKind
    # Permissions are issued by the authentication service only. The policy engine trusts a principal only if
    # `attestation` is a valid, unexpired signature from this process's authenticator over every field below,
    # so a Principal constructed anywhere else (agent, capability, worker, test) carries no authority.
    permissions: frozenset[str] = Field(default_factory=frozenset)
    authenticated: bool = False
    tenant_id: str | None = None
    role: str | None = None
    session_id: str | None = None
    expires_at: float | None = None
    attestation: str | None = None

    @classmethod
    def anonymous(cls) -> "Principal":
        return cls(id="anonymous", kind=PrincipalKind.ANONYMOUS)


class TenantContext(BaseModel):
    model_config = ConfigDict(frozen=True)
    tenant_id: str | None = None


class RequestContext(BaseModel):
    model_config = ConfigDict(frozen=True)
    request_id: str
    correlation_id: str
    principal: Principal
    tenant: TenantContext = Field(default_factory=TenantContext)
    idempotency_key: str | None = None
