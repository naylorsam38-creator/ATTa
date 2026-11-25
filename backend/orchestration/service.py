"""Builds the controller from configuration and reports its health (Stage 01A §23).

One factory used by the API process, the CLI and tests — no second startup path. An essential component failure
makes the controller NOT ready, and the gateway refuses to execute: autonomous execution never silently bypasses
the controller to keep up appearances."""
from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field
from pathlib import Path

from backend.capability_registry import CapabilityRegistry, TrustStore
from backend.capability_runtime import CapabilityRuntime
from backend.contracts import get_logger
from backend.database.session import Database
from backend.policy_engine import PolicyEngine
from backend.service_manager.health_monitor import HealthMonitor
from backend.settings import Settings
from backend.settings.loader import resolve_path

from .authorization import AuthorizationVerifier
from .catalogue import ToolCatalogue
from .config import ControllerConfig, load_controller_config
from .controller import RuntimeController
from .store import ControlStore

log = get_logger("atta.controller.service")


@dataclass
class ControllerService:
    controller: RuntimeController | None
    registry: CapabilityRegistry | None
    monitor: HealthMonitor | None
    problems: list[str] = field(default_factory=list)

    def health(self) -> dict:
        comp: dict[str, dict] = {}

        def put(name, ok, detail=""):
            comp[name] = {"ok": bool(ok), "detail": detail}
        c = self.controller
        put("controller", c is not None, "; ".join(self.problems) if c is None else c.controller_id)
        put("capability_registry", self.registry is not None and self.registry.discovered_at is not None,
            f"{len(self.registry.executable_ids())} executable" if self.registry else "")
        if c is not None:
            try:
                counts = c.store.counts()
                put("contract_storage", True, "control schema reachable")
                put("checkpoint_storage", True, "control.checkpoints reachable")
            except Exception as exc:  # noqa: BLE001
                counts = {"active": None, "blocked": None}
                put("contract_storage", False, str(exc)[:200])
                put("checkpoint_storage", False, str(exc)[:200])
            put("tool_catalogue", not c.catalogue.problems, "; ".join(c.catalogue.problems) or
                f"{len(c.catalogue.all())} numbered tools, {len(c.catalogue.unnumbered)} unnumbered")
            put("verification_service", c.verifier is not None, "independent-verifier/1")
            put("execution_worker", Path(sys.executable).is_file(), "supervised process runtime")
            from backend.policy_engine import PolicyRequest
            from backend.contracts import Principal
            deny = c.policy.evaluate(PolicyRequest(principal=Principal.anonymous(), operation="tool.execute"))
            put("security_enforcement", not deny.allowed, "anonymous tool.execute is denied" if not deny.allowed
                else "SECURITY FAILURE: anonymous execution allowed")
        else:
            counts = {"active": None, "blocked": None}
        ready = all(v["ok"] for v in comp.values()) and bool(comp)
        return {"ready": ready, "components": comp, "active_tasks": counts.get("active"),
                "blocked_tasks": counts.get("blocked")}


def lowered(cfg: ControllerConfig, caps: dict) -> ControllerConfig:
    """Profile adaptation may only LOWER the trusted controller limits (Stage 01B §18)."""
    limits = cfg.limits.model_dump()
    for k, v in (caps or {}).items():
        if k in limits:
            limits[k] = type(limits[k])(min(limits[k], v))
    return cfg.model_copy(update={"limits": cfg.limits.model_validate(limits)})


def build_controller_service(settings: Settings, *, policy: PolicyEngine | None = None,
                             start_monitor: bool = True, config: ControllerConfig | None = None) -> ControllerService:
    problems: list[str] = []
    monitor = HealthMonitor(settings, interval_s=settings.runtime.monitor_interval_s * 2,
                            max_age_s=settings.capabilities.service_health_max_age_s,
                            recovery_confirmations=settings.deployment.health_recovery_confirmations)
    if start_monitor:
        monitor.start()
    policy = policy or PolicyEngine()
    d = settings.deployment
    worker_python, site_dirs = sys.executable, None
    if os.name == "nt" and d.windows_worker_isolation:
        from backend.deployment import windows_hardening
        exe = windows_hardening.worker_exe(resolve_path(settings.runtime.directory))
        if exe.is_file():     # created by INSTALL (admin) together with the firewall rules bound to it
            worker_python, site_dirs = str(exe), windows_hardening.site_dirs()
    runtime = CapabilityRuntime(python=worker_python, site_dirs=site_dirs,
                                windows_isolation=d.windows_worker_isolation,
                                windows_allow_child_processes=d.windows_worker_child_processes,
                                memory_limit_mb=settings.workers.memory_limit_mb, network=d.worker_network,
                                worker_user=d.worker_user, cuda_visible_devices=None if not d.gpu_visible_to_workers else "0",
                                isolation_required=d.worker_network == "none")
    trust = TrustStore.load(resolve_path(settings.capabilities.trust_store),
                            resolve_path(settings.capabilities.trust_public_key))
    registry = CapabilityRegistry([resolve_path(d) for d in settings.capabilities.directories], policy=policy,
                                  service_available=monitor.is_available,
                                  healthcheck_timeout_s=settings.capabilities.healthcheck_timeout_s,
                                  max_timeout_s=settings.deadlines.max_execution_timeout_s, trust=trust,
                                  runtime=runtime, health_max_age_s=30)
    registry.discover()
    # Losing a service disables dependants at once; a RECOVERED service is revalidated (confirmed healthy probes, then
    # the dependants' own isolated health checks run again) before anything depending on it becomes available.
    monitor.on_change(lambda changed: registry.recheck_health())
    controller = None
    try:
        db = Database(settings)
        cfg = lowered(config or load_controller_config(), settings.deployment.controller_caps)
        catalogue = ToolCatalogue(registry, resolve_path(settings.capabilities.tool_numbers), db,
                                  settings.capabilities.catalogue_namespace).load()
        controller = RuntimeController(store=ControlStore(db), registry=registry, catalogue=catalogue, policy=policy,
                                       runtime=runtime, config=cfg,
                                       workspace_root=resolve_path(settings.runtime.directory) / "workspaces",
                                       authorizer=AuthorizationVerifier(resolve_path(cfg.operator_public_key)))
        monitor.on_change(lambda changed: catalogue.refresh_availability())
    except Exception as exc:  # noqa: BLE001 — reported, never bypassed
        problems.append(f"{type(exc).__name__}: {exc}"[:300])
        log.error("controller unavailable: %s", exc)
    return ControllerService(controller, registry, monitor, problems)
