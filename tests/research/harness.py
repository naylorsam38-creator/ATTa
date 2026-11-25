"""Stage 11 test harness: the REAL Stage 01A controller (PostgreSQL, supervised isolated runtime, Ed25519 trust,
policy engine, numbered catalogue) over test-signed COPIES of the Stage 11 capability packages.

Only two things differ from production, both explicit:
  * the copy of research.web's trusted retrieval policy allows loopback on the fixture server's port (the production
    package refuses loopback/private destinations; tests/research/test_url_security.py proves that);
  * the test permission catalog grants the controller the Stage 11 permissions (production permissions.yaml is an
    operator decision recorded in STAGE_11_MERGE_MANIFEST.md).
Keys, the trust store, the tool-number namespace and the workspace are generated per test. No Internet is used."""
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
from backend.database.session import Database  # noqa: E402
from backend.orchestration.authorization import AuthorizationVerifier, sign_authorization  # noqa: E402
from backend.orchestration.catalogue import ToolCatalogue  # noqa: E402
from backend.orchestration.config import load_controller_config  # noqa: E402
from backend.orchestration.controller import RuntimeController  # noqa: E402
from backend.orchestration.store import ControlStore  # noqa: E402
from backend.policy_engine import PolicyEngine  # noqa: E402
from backend.policy_engine.catalog import Catalog, load_catalog  # noqa: E402
from backend.research.config import ProviderConfig, load_research_config  # noqa: E402
from backend.research.contracts import NetworkMode  # noqa: E402
from backend.research.network import StaticNetworkMode  # noqa: E402
from backend.research.service import ResearchService  # noqa: E402
from backend.research.store import ResearchStore  # noqa: E402
from backend.settings import load_settings  # noqa: E402

STAGE11_NUMBERS = yaml.safe_load((ROOT / "documentation" / "stage11" / "tool_numbers.stage11.yaml").read_text())["tools"]
STAGE11_PERMISSIONS = yaml.safe_load((ROOT / "documentation" / "stage11" / "permissions.stage11.yaml").read_text())
PACKAGES = ["research_web", "research_evidence"]
EVIDENCE = ROOT / "evidence" / "stage_11"


@dataclass
class RH:
    root: Path
    caps: Path
    controller: RuntimeController
    service: ResearchService
    store: ResearchStore
    registry: CapabilityRegistry
    catalogue: ToolCatalogue
    operator_key: Path
    namespace: str
    network: StaticNetworkMode
    policy: PolicyEngine
    port: int | None

    def auth(self, action: str, subject: str, payload_hash: str):
        return sign_authorization(self.operator_key, action, subject, payload_hash, "test-operator")

    def approve(self, research_id: str) -> None:
        p = self.service.approval_payload(research_id)
        self.service.authorise(research_id, self.auth(p["action"], p["subject"], p["payload_hash"]))


def test_catalog() -> Catalog:
    base = load_catalog(ROOT / "configuration" / "permissions.yaml")
    perms = dict(base.permissions) | STAGE11_PERMISSIONS["permissions"]
    granted = set(base.controller_granted) | set(STAGE11_PERMISSIONS["controller_granted_permissions"])
    return Catalog(permissions=perms, controller_granted=frozenset(granted),
                   approval_required_risks=base.approval_required_risks)


def build(tmp: Path, *, port: int | None, providers: list[dict] | None = None, mode: NetworkMode = NetworkMode.ONLINE,
          production_policy: bool = False, numbers: dict | None = None, grant: bool = True,
          reuse: RH | None = None, claim_builder=None, hub=None, **cfg_overrides) -> RH:
    if reuse is not None:
        root, caps, key, ns = reuse.root, reuse.caps, reuse.operator_key, reuse.namespace
    else:
        root = tmp / f"rh-{uuid.uuid4().hex[:6]}"
        caps = root / "capabilities"
        for pkg in PACKAGES:
            shutil.copytree(ROOT / "capabilities" / pkg, caps / pkg,
                            ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        if port is not None and not production_policy:
            pol = caps / "research_web" / "retrieval_policy.py"
            text = pol.read_text()
            assert '"loopback_ports_allowed_for_tests": [],' in text
            pol.write_text(text.replace('"loopback_ports_allowed_for_tests": [],',
                                        f'"loopback_ports_allowed_for_tests": [{port}],'))
        keys = root / "keys"
        keys.mkdir()
        generate_keypair(keys / "trust.pem", keys / "trust.pub")
        generate_keypair(keys / "operator.pem", root / "operator.pub")
        for pkg in PACKAGES:
            trust_package(caps / pkg, root / "trust.json", keys / "trust.pem", "test-operator")
        key = keys / "operator.pem"
        ns = f"test-s11-{uuid.uuid4().hex[:10]}"
        table = dict(numbers if numbers is not None else {k: v["tool"] for k, v in STAGE11_NUMBERS.items()})
        (root / "tool_numbers.yaml").write_text(yaml.safe_dump({"tools": {k: {"tool": v} for k, v in table.items()}}))
    settings = load_settings("test")
    policy = PolicyEngine(catalog=test_catalog() if grant else load_catalog(ROOT / "configuration" / "permissions.yaml"))
    runtime = CapabilityRuntime()
    trust = TrustStore.load(root / "trust.json", root / "keys" / "trust.pub")
    registry = CapabilityRegistry([caps], policy=policy, trust=trust, runtime=runtime,
                                  service_available=lambda n: True, healthcheck_timeout_s=10)
    registry.discover()
    db = Database(settings)
    catalogue = ToolCatalogue(registry, root / "tool_numbers.yaml", db, ns).load()
    cfg = load_controller_config().model_copy(update={"operator_public_key": str(root / "operator.pub")})
    controller = RuntimeController(store=ControlStore(db), registry=registry, catalogue=catalogue, policy=policy,
                                   runtime=runtime, config=cfg, workspace_root=root / "workspaces",
                                   authorizer=AuthorizationVerifier(root / "operator.pub"))
    rcfg = load_research_config()
    if providers is None and port is not None:
        providers = [{"id": "fixture-searx", "kind": "searxng", "endpoint": f"http://127.0.0.1:{port}/searx",
                      "max_results": 10}]
    rcfg = rcfg.model_copy(update={"providers": [ProviderConfig.model_validate(p) for p in (providers or [])],
                                   **cfg_overrides})
    network = StaticNetworkMode(mode)
    store = ResearchStore(db)
    svc = ResearchService(controller=controller, store=store, config=rcfg, network=network,
                          claim_builder=claim_builder, hub=hub,
                          loopback_ports_for_tests=frozenset({port}) if port is not None else frozenset())
    return RH(root, caps, controller, svc, store, registry, catalogue, key, ns, network, policy, port)


def record(test_id: str, data) -> None:
    import json
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    (EVIDENCE / f"{test_id}.json").write_text(json.dumps(data, indent=2, default=str))
