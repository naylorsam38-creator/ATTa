"""Stage 12 vocabulary: integration types, trust classes, lifecycle states, health, side effects and operations.

These are DATA enums. Nothing in this module grants authority: authority comes only from operator-signed approvals,
the Stage 01A task contract / allocator / gateway / policy engine, and (from Stage 02) authenticated approvals."""
from __future__ import annotations

from enum import Enum


class IntegrationType(str, Enum):
    CONNECTOR = "CONNECTOR"
    MCP_SERVER = "MCP_SERVER"
    SKILL = "SKILL"
    PLUGIN = "PLUGIN"
    CUSTOM_PROVIDER = "CUSTOM_PROVIDER"


class TrustClass(str, Enum):
    CORE = "CORE"              # shipped with ATTa and operator-signed
    TRUSTED = "TRUSTED"        # operator-approved; runs in the Stage 01A supervised runtime
    RESTRICTED = "RESTRICTED"  # needs OS isolation (Stage 01B) and read-only side effects; BLOCKED without it
    UNTRUSTED = "UNTRUSTED"    # may be inspected, never executed
    DISABLED = "DISABLED"      # nothing at all


EXECUTABLE_TRUST = frozenset({TrustClass.CORE, TrustClass.TRUSTED, TrustClass.RESTRICTED})


class IntegrationState(str, Enum):
    DISCOVERED = "DISCOVERED"
    VALIDATING = "VALIDATING"
    AWAITING_APPROVAL = "AWAITING_APPROVAL"
    INSTALLING = "INSTALLING"
    REGISTERING = "REGISTERING"
    VERIFYING = "VERIFYING"
    INSTALLED = "INSTALLED"
    ENABLED = "ENABLED"
    DISABLED = "DISABLED"
    BLOCKED = "BLOCKED"
    FAILED = "FAILED"
    REMOVING = "REMOVING"
    REMOVED = "REMOVED"


S = IntegrationState
TRANSITIONS: dict[IntegrationState, frozenset[IntegrationState]] = {
    S.DISCOVERED: frozenset({S.VALIDATING, S.REMOVED}),
    S.VALIDATING: frozenset({S.AWAITING_APPROVAL, S.FAILED, S.BLOCKED}),
    S.AWAITING_APPROVAL: frozenset({S.INSTALLING, S.VALIDATING, S.FAILED, S.BLOCKED, S.REMOVED}),
    S.INSTALLING: frozenset({S.REGISTERING, S.FAILED}),
    S.REGISTERING: frozenset({S.VERIFYING, S.FAILED}),
    S.VERIFYING: frozenset({S.INSTALLED, S.FAILED}),
    S.INSTALLED: frozenset({S.ENABLED, S.DISABLED, S.REMOVING, S.VALIDATING, S.BLOCKED}),
    S.ENABLED: frozenset({S.DISABLED, S.REMOVING, S.VALIDATING, S.BLOCKED}),
    S.DISABLED: frozenset({S.ENABLED, S.REMOVING, S.VALIDATING, S.BLOCKED}),
    S.BLOCKED: frozenset({S.VALIDATING, S.REMOVING, S.DISABLED}),
    S.FAILED: frozenset({S.VALIDATING, S.REMOVED, S.REMOVING}),
    S.REMOVING: frozenset({S.REMOVED, S.DISABLED}),
    S.REMOVED: frozenset({S.VALIDATING}),
}
# States in which the integration's package is present in the registry directory.
INSTALLED_STATES = frozenset({S.INSTALLED, S.ENABLED, S.DISABLED, S.BLOCKED, S.REMOVING})


class HealthStatus(str, Enum):
    HEALTHY = "HEALTHY"
    DEGRADED = "DEGRADED"
    UNHEALTHY = "UNHEALTHY"
    STOPPED = "STOPPED"
    BLOCKED = "BLOCKED"
    OFFLINE_UNAVAILABLE = "OFFLINE_UNAVAILABLE"
    AUTH_REQUIRED = "AUTH_REQUIRED"


class SideEffect(str, Enum):
    READ_ONLY = "READ_ONLY"
    REVERSIBLE_WRITE = "REVERSIBLE_WRITE"
    EXTERNAL_COMMUNICATION = "EXTERNAL_COMMUNICATION"
    DESTRUCTIVE = "DESTRUCTIVE"
    FINANCIAL = "FINANCIAL"
    ADMINISTRATIVE = "ADMINISTRATIVE"
    SECURITY_SENSITIVE = "SECURITY_SENSITIVE"
    OTHER_HIGH_RISK = "OTHER_HIGH_RISK"


# A tool may declare a HIGHER risk than this floor, never a lower one (no under-declaration).
RISK_FLOOR: dict[SideEffect, str] = {
    SideEffect.READ_ONLY: "low",
    SideEffect.REVERSIBLE_WRITE: "medium",
    SideEffect.EXTERNAL_COMMUNICATION: "high",
    SideEffect.DESTRUCTIVE: "high",
    SideEffect.FINANCIAL: "critical",
    SideEffect.ADMINISTRATIVE: "high",
    SideEffect.SECURITY_SENSITIVE: "critical",
    SideEffect.OTHER_HIGH_RISK: "high",
}
RISK_ORDER = {"low": 0, "medium": 1, "high": 2, "critical": 3}


class ConnectorOperation(str, Enum):
    READ = "READ"
    SEARCH = "SEARCH"
    CREATE = "CREATE"
    UPDATE = "UPDATE"
    DELETE = "DELETE"
    SEND = "SEND"
    EXECUTE = "EXECUTE"
    ADMINISTRATIVE = "ADMINISTRATIVE"


# Minimum side-effect class per connector operation ("connected" never means "may do everything").
OPERATION_MIN_EFFECT: dict[ConnectorOperation, SideEffect] = {
    ConnectorOperation.READ: SideEffect.READ_ONLY,
    ConnectorOperation.SEARCH: SideEffect.READ_ONLY,
    ConnectorOperation.CREATE: SideEffect.REVERSIBLE_WRITE,
    ConnectorOperation.UPDATE: SideEffect.REVERSIBLE_WRITE,
    ConnectorOperation.DELETE: SideEffect.DESTRUCTIVE,
    ConnectorOperation.SEND: SideEffect.EXTERNAL_COMMUNICATION,
    ConnectorOperation.EXECUTE: SideEffect.OTHER_HIGH_RISK,
    ConnectorOperation.ADMINISTRATIVE: SideEffect.ADMINISTRATIVE,
}
READ_ONLY_CONNECTOR_OPS = frozenset({ConnectorOperation.READ, ConnectorOperation.SEARCH})


class ExecutionMode(str, Enum):
    SUPERVISED_PROCESS = "SUPERVISED_PROCESS"   # Stage 01A supervised runtime (no OS sandbox)
    ISOLATED_SANDBOX = "ISOLATED_SANDBOX"       # requires a Stage 01B isolation provider
    MCP_STDIO = "MCP_STDIO"                     # MCP server as a supervised local process
    MCP_HTTP = "MCP_HTTP"                       # MCP Streamable HTTP, subject to network policy
    DECLARATIVE = "DECLARATIVE"                 # skills: no executable code of their own


class ConnectivityMode(str, Enum):
    ONLINE = "ONLINE"
    RESTRICTED = "RESTRICTED"     # only hosts explicitly allowed by the operator
    AIR_GAPPED = "AIR_GAPPED"     # loopback only; network integrations report OFFLINE_UNAVAILABLE


# Permanent numbering: integration tools are numbered CCC.TTT with CCC = the register capability of their type and
# TTT drawn from a band reserved for Stage 12 automatic assignment (operators keep 001-499 and 900-999).
TYPE_CAPABILITY = {IntegrationType.CONNECTOR: "CAP-21", IntegrationType.MCP_SERVER: "CAP-22",
                   IntegrationType.SKILL: "CAP-23", IntegrationType.PLUGIN: "CAP-24"}
CUSTOM_PROVIDER_CAPABILITIES = frozenset({"CAP-21", "CAP-22", "CAP-24", "CAP-40"})
AUTO_NUMBER_BAND = (500, 899)
CAPABILITY_ID_PREFIX = "integration."
