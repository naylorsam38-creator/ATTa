"""Stage 12 test harness: the REAL Stage 01A harness (PostgreSQL, supervised runtime, policy engine, ONE registry,
ONE catalogue, execution gateway) with Stage 12 attached by composition. Fixture packages are copied per test into
a temporary source directory. Proposals are explicit test data (ExplicitTestProposals), never presented as model
output. Evidence for each test is written to evidence/stage_12/acceptance/<test id>.json."""
from __future__ import annotations

import json
import shutil
import sys
import uuid
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests" / "acceptance"))

from s01a_common import P, start  # noqa: E402,F401
from s01a_harness import ExplicitTestProposals, Harness, TaskSpec, build  # noqa: E402,F401

from backend.contracts import canonical_hash  # noqa: E402
from backend.database.session import Database  # noqa: E402
from backend.integrations.config import IntegrationsConfig  # noqa: E402
from backend.integrations.service import IntegrationService, attach  # noqa: E402
from backend.integrations.types import TrustClass  # noqa: E402
from backend.orchestration.authorization import AuthorizationVerifier  # noqa: E402

FIX = Path(__file__).parent / "fixtures"
EVIDENCE = ROOT / "evidence" / "stage_12" / "acceptance"


def record(test_id: str, data) -> None:
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    (EVIDENCE / f"{test_id}.json").write_text(json.dumps(data, indent=2, default=str, sort_keys=True))


@dataclass
class H12:
    h: Harness
    svc: IntegrationService
    src: Path
    config: IntegrationsConfig

    @property
    def mgr(self):
        return self.svc.manager

    @property
    def store(self):
        return self.svc.store

    def path(self, fixture: str) -> Path:
        return self.src / fixture / "integration.yaml"

    def install(self, fixture: str | dict, trust: TrustClass = TrustClass.TRUSTED, enable: bool = False) -> dict:
        source = fixture if isinstance(fixture, dict) else self.path(fixture)
        v = self.mgr.validate(source, trust)
        assert v.valid, v.problems
        auth = self.h.auth(v.approval_action, v.approval_subject, v.approval_binding)
        res = self.mgr.install(source, auth, trust_class=trust)
        if enable and res["status"] == "INSTALLED":
            self.enable(res["integration_id"])
        return res

    def enable_auth(self, iid: str, key=None):
        rec = self.mgr.get_installed(iid)
        payload = canonical_hash({"integration_id": iid, "version": rec.version, "binding": rec.binding})
        return self.h.auth("integration.enable", iid, payload, key)

    def enable(self, iid: str) -> dict:
        return self.mgr.enable(iid, self.enable_auth(iid))

    def upgrade(self, fixture: str | dict, trust: TrustClass | None = None) -> dict:
        source = fixture if isinstance(fixture, dict) else self.path(fixture)
        v = self.mgr.validate(source, trust or self.mgr.get_installed(_iid(source)).trust_class)
        assert v.valid, v.problems
        return self.mgr.upgrade(source, self.h.auth(v.approval_action, v.approval_subject, v.approval_binding),
                                trust_class=trust)

    def number(self, iid: str, tool: str) -> str:
        return self.mgr.get_installed(iid).numbers[tool]

    def workspace(self, objective_id: str) -> Path:
        return self.h.controller.gateway.workspace(objective_id)

    def objective(self, n: int = 1):
        return self.h.objective(n_reqs=n)

    def spec(self, task_id: str, task_type: str, operations: list[str], tool_id: str, *, paths=("notes.json",),
             max_risk: str = "low", **kw) -> TaskSpec:
        crit = kw.pop("criteria", [
            {"id": "TC-01", "description": "the integration tool executed", "requirement_ids": ["FR-01"],
             "verifier": "execution_succeeded", "params": {"tool_id": tool_id}},
            {"id": "TC-02", "description": "independently verified output", "requirement_ids": ["FR-01"],
             "verifier": "tool_verify", "params": {"tool_id": tool_id}}])
        return TaskSpec(task_id=task_id, requirement_ids=["FR-01"], task_type=task_type,
                        description=f"use {tool_id}", required_outcome="integration result obtained",
                        operations=operations, acceptance_criteria=crit, permitted_filesystem_paths=list(paths),
                        max_risk=max_risk, **kw)


def _iid(source) -> str:
    import yaml
    if isinstance(source, dict):
        return source["integration_id"]
    return yaml.safe_load(Path(source).read_text())["integration_id"]


def stage01b_runtime_present() -> bool:
    import inspect
    from backend.capability_runtime import CapabilityRuntime
    return "isolation_required" in inspect.signature(CapabilityRuntime.__init__).parameters


def build12(tmp: Path, *, connectivity: str = "ONLINE", isolation=None, broker=None, allowed_hosts=(),
            reuse: H12 | None = None, fixtures: list[str] | None = None, worker_network: str | None = None,
            connectivity_provider=None, settings=None, **cfg) -> H12:
    if reuse is not None:
        h = build(tmp, reuse=reuse.h)
        src = reuse.src
        config = reuse.config
    else:
        h = build(tmp)
        src = h.root / "integrations_src"
        src.mkdir()
        for d in FIX.iterdir():
            if d.is_dir() and (fixtures is None or d.name in fixtures):
                shutil.copytree(d, src / d.name, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        config = IntegrationsConfig(source_directories=[str(src)], install_root=str(h.root / "integrations"),
                                    connectivity=connectivity, allowed_network_hosts=list(allowed_hosts),
                                    mcp_definitions=None, health_max_age_s=0, mcp_request_timeout_s=20,
                                    mcp_startup_timeout_s=10, **cfg)
    if worker_network is not None:          # Stage 01B runtime only: real per-handler isolation
        assert stage01b_runtime_present(), "Stage 01B runtime not present"
        h.controller.runtime.network = worker_network
        h.controller.runtime.isolation_required = worker_network == "none"
    svc = attach(h.controller, db=Database(h.settings), config=config,
                 authorizer=AuthorizationVerifier(h.root / "operator.pub"), namespace=h.namespace,
                 isolation=isolation, broker=broker, connectivity_provider=connectivity_provider,
                 settings=settings)
    return H12(h, svc, src, config)


def write_notes(ws: Path) -> None:
    (ws / "notes.json").write_text(json.dumps([
        {"id": "n1", "title": "Budget review", "body": "Quarterly budget notes"},
        {"id": "n2", "title": "Holiday", "body": "Office closed on Friday"}]))


def new_task_id() -> str:
    return f"TASK-{uuid.uuid4().int % 9000 + 1000:04d}"


__all__ = ["stage01b_runtime_present", "build12", "H12", "record", "write_notes", "P", "start", "ExplicitTestProposals", "TaskSpec",
           "TrustClass", "canonical_hash", "FIX", "ROOT"]
