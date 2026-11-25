"""IntegrationService — the typed backend contract Stage 04 will consume, and attach(): the ONE place where Stage 12
is wired into an existing Stage 01A controller by composition:

  registry.trust        -> IntegrationTrustGate (delegates every non-integration capability to the Stage 01A store)
  registry.directories  += <install_root>/packages      (the same registry discovers generated packages)
  catalogue.extra_numbers = Stage 12 permanent numbers   (the same catalogue, the same control.tool_identifiers)
  gateway.guards        += ExecutionGuard                (refuse only)
  gateway.result_filters+= ResultFilter                  (keep or reject only)
  gateway.execute        = RejectionAuditor(execute)     (audit only)
  controller.allocator   = AuditingAllocator(allocator)  (audit only; decisions unchanged)
  controller.plan/submit_contract = PlanPinner(...)       (records version pins only)

Nothing here gives a model, a worker or an integration a way to install, enable, trust, approve or grant."""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from backend.capability_registry.numbering import NumberEntry
from backend.contracts import get_logger
from backend.orchestration.authorization import AuthorizationVerifier, OperatorAuthorization
from backend.orchestration.controller import TaskSpec

from .config import IntegrationsConfig, load_integrations_config
from .manager import IntegrationError, IntegrationManager, _with_dir
from .runtime import (AuditingAllocator, ExecutionGuard, IntegrationTrustGate, PlanPinner, RejectionAuditor,
                      ResultFilter)
from .skills import ScriptProposalSource, SkillContent, pin_key
from .store import IntegrationStore
from .types import IntegrationType, TrustClass

log = get_logger("atta.integrations.service")


# ---------------------------------------------------------------------- typed contracts (API / Stage 04)
class _Out(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ToolInfo(_Out):
    tool_id: str
    string_id: str
    name: str
    description: str
    side_effect: str
    connector_operation: str | None = None
    operations: list[str]
    task_types: list[str]
    risk: str
    permissions: list[str]
    input_schema: dict
    output_schema: dict
    error_schema: dict
    timeout_s: float
    cancellable: bool
    approval_required: bool
    available: bool


class IntegrationSummary(_Out):
    integration_id: str
    integration_type: str
    name: str
    provider: str
    version: str | None
    state: str
    trust_class: str
    enabled: bool
    installed_at: str | None = None
    updated_at: str | None = None


class IntegrationDetail(IntegrationSummary):
    description: str | None = None
    manifest_version: int | None = None
    capabilities: list[dict] = Field(default_factory=list)
    tools: list[ToolInfo] = Field(default_factory=list)
    permissions_requested: list[str] = Field(default_factory=list)
    network_requirements: dict = Field(default_factory=dict)
    credential_requirements: list[dict] = Field(default_factory=list)
    credential_refs: list[str] = Field(default_factory=list)
    execution_mode: str | None = None
    risk_class: str | None = None
    configuration_schema: dict = Field(default_factory=dict)
    integrity: dict = Field(default_factory=dict)
    problems: list[str] = Field(default_factory=list)


class OperatorAuthorizationIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: str
    subject: str
    payload_hash: str
    operator: str
    expires_at: float
    signature: str

    def to_auth(self) -> OperatorAuthorization:
        return OperatorAuthorization(**self.model_dump())


class InstallRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source: str
    trust_class: TrustClass = TrustClass.TRUSTED
    authorization: OperatorAuthorizationIn | None = None


class ValidateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source: str
    trust_class: TrustClass = TrustClass.TRUSTED


class AuthorizedRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    authorization: OperatorAuthorizationIn | None = None


class DisableRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reason: str = Field("disabled by administrator", max_length=500)


# ---------------------------------------------------------------------- service
class IntegrationService:
    def __init__(self, manager: IntegrationManager, controller=None):
        self.manager, self.controller = manager, controller
        self.store = manager.store

    # ---- queries
    def list(self, integration_type: IntegrationType | None = None) -> list[IntegrationSummary]:
        out = []
        for r in self.store.integrations():
            if integration_type and r["integration_type"] != integration_type.value:
                continue
            out.append(self._summary(r))
        return out

    def _summary(self, r: dict) -> IntegrationSummary:
        return IntegrationSummary(integration_id=r["integration_id"], integration_type=r["integration_type"],
                                  name=r["name"], provider=r["provider"], version=r["current_version"], state=r["state"],
                                  trust_class=r["trust_class"], enabled=r["state"] == "ENABLED",
                                  installed_at=r["installed_at"].isoformat() if r["installed_at"] else None,
                                  updated_at=r["updated_at"].isoformat() if r["updated_at"] else None)

    def get(self, integration_id: str) -> IntegrationDetail:
        r = self.store.integration(integration_id)
        if r is None:
            raise IntegrationError(f"unknown integration '{integration_id}'", status="NOT_FOUND")
        rec = self.manager.get_installed(integration_id)
        raw = rec.manifest if rec else (self.store.manifest(r["approved_manifest_digest"]) or {}).get("manifest") \
            if r["approved_manifest_digest"] else {}
        raw = raw or {}
        from .types import RISK_ORDER
        risks = [t["risk"] for t in raw.get("tools") or []]
        return IntegrationDetail(
            **self._summary(r).model_dump(), description=raw.get("description"),
            manifest_version=raw.get("manifest_version"), capabilities=list(raw.get("capabilities") or []),
            tools=self.tools(integration_id) if rec else [],
            permissions_requested=list(raw.get("permissions_requested") or []),
            network_requirements=dict(raw.get("network_requirements") or {}),
            credential_requirements=list(raw.get("credential_requirements") or []),
            credential_refs=[c["ref"] for c in self.store.credential_refs(integration_id)
                             if rec and c["version"] == rec.version],
            execution_mode=raw.get("execution_mode"),
            risk_class=max(risks, key=lambda x: RISK_ORDER[x]) if risks else None,
            configuration_schema=dict(raw.get("configuration_schema") or {}),
            integrity={"manifest_digest": r["approved_manifest_digest"], "package_digest": r["approved_package_digest"],
                       "approval_binding": r["approval_binding"]},
            problems=list(r["problems"] or []))

    def tools(self, integration_id: str) -> list[ToolInfo]:
        rec = self.manager.get_installed(integration_id)
        if rec is None:
            return []
        out = []
        cat = self.manager.catalogue
        catalog = self.manager.catalog
        from .packaging import tool_output_schema
        from .manifest import IntegrationManifest
        for t in rec.tools:
            n = rec.numbers.get(t["name"])
            ct = cat.get(n) if n else None
            m = IntegrationManifest(rec.manifest, __import__("pathlib").Path("."), rec.manifest_digest, None)
            out.append(ToolInfo(tool_id=n or "", string_id=f"{rec.capability_id}:{t['name']}", name=t["name"],
                                description=t["description"], side_effect=t["side_effect"],
                                connector_operation=t.get("connector_operation"), operations=t["operations"],
                                task_types=t["task_types"], risk=t["risk"], permissions=sorted(t["permissions"]),
                                input_schema=t["input_schema"], output_schema=tool_output_schema(m, t),
                                error_schema=t["error_schema"], timeout_s=t["timeout_s"], cancellable=t["cancellable"],
                                approval_required=t["risk"] in catalog.approval_required_risks,
                                available=bool(ct and ct.available)))
        return out

    def capabilities(self, integration_id: str) -> list[dict]:
        return [{k: v for k, v in c.items() if k != "namespace"} for c in self.store.capabilities(integration_id)]

    def permissions(self, integration_id: str) -> dict:
        rows = self.store.permissions(integration_id)
        rec = self.manager.get_installed(integration_id)
        cur = [r for r in rows if rec and r["version"] == rec.version]
        catalog = self.manager.catalog
        approved = sorted({r["permission"] for r in cur if r["source"] == "approved"})
        return {"integration_id": integration_id, "version": rec.version if rec else None, "approved": approved,
                "controller_granted": sorted(set(approved) & catalog.controller_granted),
                "not_granted": sorted(set(approved) - catalog.controller_granted),
                "note": "approval of an integration never grants a task permission: tools are usable only when the "
                        "Stage 01A allocator assigns them to a task and the policy engine allows the exact call"}

    def configuration_schema(self, integration_id: str) -> dict:
        return self.get(integration_id).configuration_schema

    def health(self, integration_id: str, max_age_s: float | None = None) -> dict:
        return self.manager.health(integration_id, max_age_s=max_age_s)

    def mcp_servers(self) -> list[dict]:
        return [{k: (v.isoformat() if hasattr(v, "isoformat") else v) for k, v in r.items() if k != "namespace"}
                for r in self.store.mcp_servers()]

    def mcp_capabilities(self, integration_id: str) -> list[dict]:
        return [{k: (v.isoformat() if hasattr(v, "isoformat") else v) for k, v in r.items() if k != "namespace"}
                for r in self.store.mcp_capabilities(integration_id)]

    def skills(self) -> list[dict]:
        out = []
        for s in self.store.skills():
            out.append({"skill_id": s["skill_id"], "integration_id": s["integration_id"],
                        "current_version": s["current_version"], "state": s["state"],
                        "versions": [{"version": v["version"], "content_digest": v["content_digest"]}
                                     for v in self.store.skill_versions(s["skill_id"])]})
        return out

    def plugins(self) -> list[dict]:
        return [{k: (v.isoformat() if hasattr(v, "isoformat") else v) for k, v in p.items() if k != "namespace"}
                for p in self.store.plugins()]

    def events(self, integration_id: str | None = None) -> list[dict]:
        return [{k: (v.isoformat() if hasattr(v, "isoformat") else v) for k, v in e.items() if k != "namespace"}
                for e in self.store.events(integration_id)]

    # ---- lifecycle (operator)
    def discover(self):
        return [i.__dict__ for i in self.manager.discover()]

    def validate(self, source, trust_class: TrustClass = TrustClass.TRUSTED):
        return self.manager.validate(source, trust_class).__dict__

    def install(self, source, auth, trust_class: TrustClass = TrustClass.TRUSTED):
        return self.manager.install(source, auth, trust_class=trust_class)

    def enable(self, integration_id: str, auth):
        return self.manager.enable(integration_id, auth)

    def disable(self, integration_id: str, actor: str, reason: str = "disabled by administrator"):
        return self.manager.disable(integration_id, actor, reason)

    def remove(self, integration_id: str, actor: str):
        return self.manager.remove(integration_id, actor)

    def upgrade(self, source, auth, trust_class: TrustClass | None = None):
        return self.manager.upgrade(source, auth, trust_class=trust_class)

    # ---- Hub view (what Qwen/the configured model may DISCOVER; never what it may execute)
    def list_scripts(self) -> list[dict]:
        """Enabled skills' scripts/workflows as data (id, version, description, parameter schema, tool numbers).
        Using one still requires a task contract; the allocator decides the pool."""
        out = []
        for s in self.store.skills():
            if s["state"] != "ENABLED":
                continue
            row = self.store.skill_version(s["skill_id"], s["current_version"])
            sc = SkillContent.from_row(row)
            for wf in sc.workflows:
                out.append({"skill_id": sc.skill_id, "skill_version": sc.version, "workflow_id": wf["id"],
                            "workflow_version": wf["version"], "kind": wf.get("kind", "workflow"),
                            "description": wf["description"], "parameters": wf["parameters"],
                            "tool_ids": sorted({sc.tool_references[st["tool"]] for st in wf["steps"]})})
        return out

    def skill_content(self, skill_id: str, version: str | None = None) -> SkillContent:
        s = self.store.skill(skill_id)
        if s is None:
            raise IntegrationError(f"unknown skill '{skill_id}'", status="NOT_FOUND")
        row = self.store.skill_version(skill_id, version or s["current_version"])
        if row is None:
            raise IntegrationError(f"skill {skill_id} has no version {version}", status="NOT_FOUND")
        return SkillContent.from_row(row)

    def script_task_spec(self, skill_id: str, workflow_id: str, *, task_id: str, requirement_ids: list[str],
                         task_type: str, version: str | None = None, **spec_fields) -> TaskSpec:
        """A TaskSpec whose PROPOSED pool is the script's tools and whose contract pins the skill version."""
        s = self.store.skill(skill_id)
        if s is None or s["state"] != "ENABLED":
            raise IntegrationError(f"skill '{skill_id}' is not enabled", status="REFUSED")
        sc = self.skill_content(skill_id, version)
        wf = next((w for w in sc.workflows if w["id"] == workflow_id), None)
        if wf is None:
            raise IntegrationError(f"no workflow '{workflow_id}' in {skill_id}@{sc.version}", status="NOT_FOUND")
        tools = sorted({sc.tool_references[st["tool"]] for st in wf["steps"]})
        ops = list(dict.fromkeys(st["operation"] for st in wf["steps"]))
        checks = dict(spec_fields.pop("input_checksums", {}) or {})
        checks[pin_key(skill_id, sc.version)] = sc.content_digest
        return TaskSpec(task_id=task_id, requirement_ids=requirement_ids, task_type=task_type,
                        description=spec_fields.pop("description", f"run script {skill_id}:{workflow_id}"),
                        required_outcome=spec_fields.pop("required_outcome", wf["description"]),
                        operations=ops, proposed_tool_ids=tools, input_checksums=checks, **spec_fields)

    def script_source(self, contract, skill_id: str, workflow_id: str, parameters: dict) -> ScriptProposalSource:
        """Proposal source for the skill version PINNED in the task contract (never the newest one)."""
        pins = [k for k in contract.input_checksums if k.startswith(f"skill:{skill_id}@")]
        if not pins:
            raise IntegrationError(f"task {contract.task_id} does not pin skill {skill_id}", status="REFUSED")
        version = pins[0].split("@", 1)[1]
        sc = self.skill_content(skill_id, version)
        if sc.content_digest != contract.input_checksums[pins[0]]:
            raise IntegrationError("pinned skill content digest mismatch", status="REFUSED")
        return ScriptProposalSource(sc, workflow_id, contract.task_id, list(contract.requirement_ids), parameters,
                                    contract.contract_version)


# ---------------------------------------------------------------------- wiring
def attach(controller, *, db, config: IntegrationsConfig | None = None, authorizer: AuthorizationVerifier,
           namespace: str = "default", network=None, isolation=None, broker=None, install_root=None,
           settings=None, connectivity_provider=None) -> IntegrationService:
    """settings / connectivity_provider: Stage 01B settings and/or a Stage 11 / 01B `current_mode()` provider.
    isolation defaults to the controller's own runtime (Stage 01B isolation when present, honestly unavailable
    otherwise)."""
    from .adapters import ConnectivitySources, RuntimeIsolation
    config = config or load_integrations_config()
    if network is None:
        network = ConnectivitySources(config.connectivity, settings, connectivity_provider).policy(
            config.allowed_network_hosts)
    if isolation is None:
        isolation = RuntimeIsolation(controller.runtime)
    store = IntegrationStore(db, namespace)
    registry, catalogue, gateway = controller.registry, controller.catalogue, controller.gateway
    manager = IntegrationManager(store=store, registry=registry, catalogue=catalogue, catalog=controller.policy.catalog,
                                 config=config, authorizer=authorizer, network=network, isolation=isolation,
                                 broker=broker, install_root=install_root)
    if not isinstance(registry.trust, IntegrationTrustGate):
        registry.trust = IntegrationTrustGate(registry.trust, manager)
    registry.directories = _with_dir(registry.directories, manager.packages_dir)
    catalogue.extra_numbers = lambda: {r["number"]: NumberEntry(r["number"], r["tool_string"], False)
                                       for r in store.numbers()}
    problems = manager.load_state()
    registry.discover()
    catalogue.load()
    gateway.guards.append(ExecutionGuard(manager))
    gateway.result_filters.append(ResultFilter(manager))
    gateway.execute = RejectionAuditor(manager, gateway.execute)
    controller.allocator = AuditingAllocator(controller.allocator, manager)
    controller.plan = PlanPinner(manager, controller.plan)
    controller.submit_contract = PlanPinner(manager, controller.submit_contract)
    for p in problems:
        log.warning("integration start: %s", p)
    return IntegrationService(manager, controller)


def attach_to_controller_service(svc, settings) -> IntegrationService | None:
    """Production wiring used by the API lifespan. A failure here never blocks ATTa start (network integrations are
    never mandatory); it is logged and integrations stay unavailable."""
    if svc is None or svc.controller is None:
        return None
    try:
        from backend.database.session import Database
        from backend.settings.loader import resolve_path
        c = svc.controller
        return attach(c, db=Database(settings), authorizer=AuthorizationVerifier(resolve_path(c.config.operator_public_key)),
                      namespace=settings.capabilities.catalogue_namespace, settings=settings)
    except Exception as exc:  # noqa: BLE001
        log.error("integrations unavailable: %s", exc)
        return None


__all__ = ["IntegrationService", "attach", "attach_to_controller_service", "IntegrationSummary", "IntegrationDetail",
           "ToolInfo", "InstallRequest", "ValidateRequest", "AuthorizedRequest", "DisableRequest", "Any"]
