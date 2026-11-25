"""Stage 01A test harness: a REAL controller (PostgreSQL, supervised runtime, policy engine, registry) over the
harmless fixture capabilities, with keys and a catalogue namespace generated per test so nothing leaks into the
production catalogue. Proposals are explicit test data (ExplicitTestProposals), never presented as model output."""
from __future__ import annotations

import shutil
import sys
import uuid
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import yaml  # noqa: E402

from backend.capability_registry import CapabilityRegistry, TrustStore  # noqa: E402
from backend.capability_registry.trust import generate_keypair, trust_package  # noqa: E402
from backend.capability_runtime import CapabilityRuntime  # noqa: E402
from backend.contracts import canonical_hash, new_id  # noqa: E402
from backend.database.session import Database  # noqa: E402
from backend.orchestration.authorization import AuthorizationVerifier, sign_authorization  # noqa: E402
from backend.orchestration.catalogue import ToolCatalogue  # noqa: E402
from backend.orchestration.config import load_controller_config  # noqa: E402
from backend.orchestration.controller import RuntimeController, TaskSpec  # noqa: E402
from backend.orchestration.models import ExplicitTestProposals  # noqa: E402
from backend.orchestration.store import ControlStore  # noqa: E402
from backend.policy_engine import PolicyEngine  # noqa: E402
from backend.settings import load_settings  # noqa: E402

FIXTURES = Path(__file__).parent / "fixtures_s01a" / "capabilities"
TRUSTED = ["test_text", "test_ops", "test_external", "test_risky", "test_needs_redis", "test_unhealthy", "test_chained"]
NUMBERS = {
    "014.001": "test.text:write_text", "014.002": "test.text:read_text", "014.003": "test.text:append_text",
    "014.004": "test.text:validate_text", "014.005": "test.text:count_words", "014.006": "test.text:fail_always",
    "040.901": "test.ops:sleep_and_mark", "040.902": "test.ops:noop", "040.906": "test.ops:record_url", "021.001": "test.external:send",
    "019.001": "test.risky:run", "040.903": "test.needs_redis:noop", "040.904": "test.unhealthy:default",
    "040.905": "test.chained:default",
}


@dataclass
class Harness:
    root: Path
    caps: Path
    controller: RuntimeController
    registry: CapabilityRegistry
    store: ControlStore
    catalogue: ToolCatalogue
    operator_key: Path
    namespace: str
    numbers_file: Path
    settings: object

    def auth(self, action: str, subject: str, payload_hash: str, key: Path | None = None):
        return sign_authorization(key or self.operator_key, action, subject, payload_hash, "test-operator")

    def objective(self, n_reqs: int = 3, approve: bool = True, integration_checks=None, instructions=None):
        reqs = [{"id": f"FR-{i:02d}", "text": f"Requirement {i}: produce part {i} of the report"} for i in range(1, n_reqs + 1)]
        c = self.controller.create_objective(
            instructions or "Write a short report in report.txt that contains the agreed sections.",
            required_deliverables=[{"id": "DEL-01", "description": "report.txt", "requirement_ids": [r["id"] for r in reqs]}],
            functional_requirements=reqs, non_functional_requirements=[], restrictions=["only the task workspace"],
            required_capabilities=["014"],
            acceptance_criteria=[{"id": "AC-01", "description": "all parts present",
                                  "requirement_ids": [r["id"] for r in reqs]}],
            completion_conditions=["every requirement verified"], integration_checks=integration_checks or [])
        if approve:
            c = self.controller.approve_objective(c.objective_id, self.auth("approve_objective", c.objective_id, c.hash()))
        return c

    def write_task(self, task_id: str, req: str, path: str, phrase: str, deps=None, **kw) -> TaskSpec:
        return TaskSpec(task_id=task_id, requirement_ids=[req], task_type="document.text",
                        description=f"write {path} containing '{phrase}'", required_outcome=f"{path} contains {phrase}",
                        operations=kw.pop("operations", ["write", "edit", "validate"]), dependency_task_ids=deps or [],
                        required_outputs=[{"id": "OUT-01", "path": path, "description": "text file"}],
                        permitted_filesystem_paths=kw.pop("paths", [path]),
                        permitted_resource_ids=kw.pop("resources", [f"file:{path}"]),
                        acceptance_criteria=kw.pop("criteria", [
                            {"id": "TC-01", "description": "file exists", "requirement_ids": [req],
                             "verifier": "output_exists", "params": {"path": path}},
                            {"id": "TC-02", "description": "phrase present", "requirement_ids": [req],
                             "verifier": "text_contains", "params": {"path": path, "all": [phrase]},
                             "repair_tool_ids": ["014.003"]}]), **kw)

    @staticmethod
    def write_proposals(task_id: str, req: str, path: str, text: str, version: int = 1, pid: str | None = None):
        pid = pid or f"p-{task_id}-{uuid.uuid4().hex[:8]}"
        return [{"proposal_id": pid, "task_id": task_id, "contract_version": version, "tool_id": "014.001",
                 "operation": "write", "arguments": {"path": path, "text": text}, "target_resources": [f"file:{path}"],
                 "expected_result": f"{path} written", "referenced_requirement_ids": [req]},
                {"proposal_id": pid + "-done", "task_id": task_id, "contract_version": version, "claim": "complete",
                 "evidence_ids": []}]

    def run_single(self, objective_id: str, task_id: str, proposals: list) -> str:
        released = self.controller.release(objective_id)
        assert task_id in released, (task_id, released)
        return self.controller.run_task(objective_id, task_id, ExplicitTestProposals(proposals))


def build(tmp: Path, *, trusted=TRUSTED, extra_numbers: dict | None = None, numbers: dict | None = None,
          service_available=None, namespace: str | None = None, policy: PolicyEngine | None = None,
          config=None, controller_id: str | None = None, reuse: Harness | None = None) -> Harness:
    """reuse: build a NEW controller process-equivalent over the same files, DB namespace and workspace (restart)."""
    if reuse is not None:
        root, caps, key, ns, numbers_file = reuse.root, reuse.caps, reuse.operator_key, reuse.namespace, reuse.numbers_file
    else:
        root = tmp / f"h-{uuid.uuid4().hex[:6]}"
        caps = root / "capabilities"
        shutil.copytree(FIXTURES, caps, ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "README.md"))
        keys = root / "keys"
        keys.mkdir()
        generate_keypair(keys / "trust.pem", keys / "trust.pub")
        generate_keypair(keys / "operator.pem", root / "operator.pub")
        for d in trusted:
            trust_package(caps / d, root / "trust.json", keys / "trust.pem", "test-operator")
        key = keys / "operator.pem"
        ns = namespace or f"test-{uuid.uuid4().hex[:12]}"
        numbers_file = root / "tool_numbers.yaml"
        table = dict(numbers if numbers is not None else NUMBERS)
        table.update(extra_numbers or {})
        numbers_file.write_text(yaml.safe_dump({"tools": {k: {"tool": v} for k, v in table.items()}}))
    settings = load_settings("test")
    policy = policy or PolicyEngine()
    runtime = CapabilityRuntime()
    trust = TrustStore.load(root / "trust.json", root / "keys" / "trust.pub")
    registry = CapabilityRegistry([caps], policy=policy, trust=trust, runtime=runtime,
                                  service_available=service_available or (lambda n: True), healthcheck_timeout_s=10)
    registry.discover()
    db = Database(settings)
    catalogue = ToolCatalogue(registry, numbers_file, db, ns).load()
    cfg = config or load_controller_config()
    cfg = cfg.model_copy(update={"operator_public_key": str(root / "operator.pub")})
    store = ControlStore(db)
    controller = RuntimeController(store=store, registry=registry, catalogue=catalogue, policy=policy, runtime=runtime,
                                   config=cfg, workspace_root=root / "workspaces",
                                   authorizer=AuthorizationVerifier(root / "operator.pub"), controller_id=controller_id)
    return Harness(root, caps, controller, registry, store, catalogue, key, ns, numbers_file, settings)


__all__ = ["build", "Harness", "TaskSpec", "ExplicitTestProposals", "canonical_hash", "new_id", "NUMBERS", "ROOT"]
