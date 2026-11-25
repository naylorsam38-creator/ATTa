"""IntegrationManager — the Stage 12 lifecycle (Stage 12 §7-§11, §17, §19, §23, §31, §34, §38-§40).

  EXTERNAL INTEGRATION -> DISCOVERY -> MANIFEST -> VALIDATION -> TRUST CLASSIFICATION -> (operator approval)
  -> INSTALLING -> REGISTERING (existing registry + permanent numbers) -> VERIFYING (independent) -> INSTALLED
  -> ENABLED (operator) ... task allocation, policy, approval and execution stay with the Stage 01A controller.

Authority: every authority-increasing action (install, upgrade, enable, trust change) needs an Ed25519 operator
authorisation bound to the exact payload (same mechanism as Stage 01A objective approval; Stage 02 replaces it with
authenticated human approval). Authority-reducing actions (disable, remove) never need one. No method accepts a
request from an integration: integration code runs only inside the supervised runtime and has no handle on this
object, and the model reaches tools only through its task pool."""
from __future__ import annotations

import os
import shutil
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

import yaml

from backend.contracts import canonical_hash, get_logger
from backend.orchestration.authorization import AuthorizationError, AuthorizationVerifier, OperatorAuthorization

from .assessment import Assessment, Installed, assess, needs_isolation
from .config import IntegrationsConfig
from .content import injection_findings
from .credentials import UnavailableCredentialBroker, credential_ref
from .isolation import NoOsSandbox
from .manifest import (MANIFEST_FILENAME, IntegrationManifest, IntegrationManifestError, load_integration_manifest,
                       validate_manifest_data)
from .mcp import client as mcp_client
from .mcp.validation import check_server
from .network import NetworkPolicy
from .packaging import build_package, capability_id_for, has_executable_tools
from .skills import build_skill_content
from .store import IntegrationStore
from .types import (AUTO_NUMBER_BAND, INSTALLED_STATES, TRANSITIONS, HealthStatus, IntegrationState, IntegrationType,
                    TrustClass)
from .upgrade import compare
from .verification import IntegrationVerifier

log = get_logger("atta.integrations")
S = IntegrationState
SECURITY_MARKERS = ("instruction-like text", "raw credential", "wildcard permissions", "below the minimum",
                    "cannot be classified READ_ONLY", "weaker than", "shell or launcher", "inline code",
                    "operator allowlist", "embed credentials", "non-local references")


class IntegrationError(Exception):
    def __init__(self, message: str, *, problems: list[str] | None = None, status: str = "FAILED"):
        super().__init__(message)
        self.problems, self.status = problems or [], status


@dataclass
class DiscoveryItem:
    integration_id: str
    source: str
    version: str | None
    integration_type: str | None
    valid: bool
    problems: list[str] = field(default_factory=list)
    manifest_digest: str | None = None
    installed_version: str | None = None


@dataclass
class ValidationResult:
    integration_id: str
    version: str
    state: str
    valid: bool
    problems: list[str]
    manifest_digest: str | None = None
    package_digest: str | None = None
    approval_action: str | None = None
    approval_subject: str | None = None
    approval_binding: str | None = None
    upgrade_report: dict | None = None


def approval_binding(m: IntegrationManifest, trust_class: TrustClass, upgrade_report: dict | None) -> str:
    """Hash of exactly what the operator approves: identity, bytes, trust, permissions, every tool's schemas,
    side effect and risk, network hosts, credential requirements and (for upgrades) the diff."""
    return canonical_hash({
        "integration_id": m.integration_id, "version": m.version, "manifest_digest": m.manifest_digest,
        "package_digest": m.package_digest, "trust_class": trust_class.value,
        "permissions": sorted(m.permissions), "network_hosts": sorted(m.network_hosts),
        "credentials": sorted(m.credential_names),
        "tools": {t["name"]: {"side_effect": t["side_effect"], "risk": t["risk"], "permissions": sorted(t["permissions"]),
                              "schemas": canonical_hash([t["input_schema"], t["output_schema"], t["error_schema"]])}
                  for t in m.tools},
        "upgrade_report": (upgrade_report or {}).get("report_hash")})


class IntegrationManager:
    def __init__(self, *, store: IntegrationStore, registry, catalogue, catalog, config: IntegrationsConfig,
                 authorizer: AuthorizationVerifier, network: NetworkPolicy | None = None, isolation=None,
                 broker=None, install_root: Path | None = None, active_tasks: Callable | None = None):
        self.store, self.registry, self.catalogue, self.catalog = store, registry, catalogue, catalog
        self.config, self.authorizer = config, authorizer
        self.network = network or NetworkPolicy(config.connectivity, frozenset(config.allowed_network_hosts))
        self.isolation = isolation or NoOsSandbox()
        self.broker = broker or UnavailableCredentialBroker()
        self.install_root = install_root or config.path(config.install_root)
        self.packages_dir = self.install_root / "packages"
        self.staging_dir = self.install_root / "staging"
        self.backups_dir = self.install_root / "backups"      # never scanned by the registry
        self.backups_dir.mkdir(parents=True, exist_ok=True)
        self.packages_dir.mkdir(parents=True, exist_ok=True)
        self.staging_dir.mkdir(parents=True, exist_ok=True)
        self.active_tasks = active_tasks or store.active_tasks_using
        self.verifier = IntegrationVerifier(registry, catalogue, catalog, store)
        self.fault_hook: Callable[[str, str], None] | None = None     # tests: inject a failure at a named step
        self._installed: dict[str, Installed] = {}
        self._lock = threading.RLock()
        self._health_cache: dict[str, tuple[float, dict]] = {}

    # ================================================================== lookups
    def get_installed(self, integration_id: str) -> Installed | None:
        with self._lock:
            return self._installed.get(integration_id)

    def by_capability(self, capability_id: str) -> Installed | None:
        with self._lock:
            return next((r for r in self._installed.values() if r.capability_id == capability_id), None)

    def by_number(self, number: str) -> Installed | None:
        with self._lock:
            return next((r for r in self._installed.values() if number in r.numbers.values()), None)

    def _put(self, rec: Installed) -> None:
        with self._lock:
            self._installed[rec.integration_id] = rec

    def _drop(self, integration_id: str) -> None:
        with self._lock:
            self._installed.pop(integration_id, None)

    def known_permissions(self) -> frozenset[str]:
        return frozenset(self.catalog.permissions)

    def _allowed_exe(self) -> frozenset[str]:
        return frozenset(self.config.allowed_mcp_executables)

    def _fault(self, step: str, integration_id: str) -> None:
        if self.fault_hook is not None:
            self.fault_hook(step, integration_id)

    # ================================================================== startup / restart persistence
    def load_state(self) -> list[str]:
        """Rebuild the in-memory view from the database and verify each installed package's bytes."""
        problems = []
        for backup in self.backups_dir.glob("*--*"):              # an upgrade interrupted by a crash: restore
            cap = backup.name.rsplit("--", 1)[0]
            target = self.packages_dir / cap
            shutil.rmtree(target, ignore_errors=True)
            os.replace(backup, target)
            problems.append(f"{cap}: interrupted upgrade rolled back to the previous package")
        shutil.rmtree(self.staging_dir, ignore_errors=True)
        self.staging_dir.mkdir(parents=True, exist_ok=True)
        for row in self.store.integrations():
            state = S(row["state"])
            if state in (S.INSTALLING, S.REGISTERING, S.VERIFYING):
                if not row["approved_manifest_digest"] and row["capability_id"] is None:
                    shutil.rmtree(self.packages_dir / capability_id_for(row["integration_id"]), ignore_errors=True)
                self.store.set_tool_status(row["integration_id"], None, "rolled_back", only="registered") \
                    if not row["approved_manifest_digest"] else None
                to = S.BLOCKED if row["approved_manifest_digest"] else S.FAILED
                self.store.set_state(row["integration_id"], to.value, "installation interrupted (restart); rolled back",
                                     "integration-manager", version=row["current_version"], from_state=state.value)
                problems.append(f"{row['integration_id']}: interrupted {state.value} rolled back")
                state = to
                if to is S.FAILED:
                    continue
            if state not in INSTALLED_STATES or not row["approved_manifest_digest"]:
                continue
            snap = self.store.manifest(row["approved_manifest_digest"])
            if snap is None:
                problems.append(f"{row['integration_id']}: approved manifest snapshot missing")
                continue
            numbers = {t["tool_name"]: t["tool_number"] for t in self.store.tools(row["integration_id"])
                       if t["version"] == row["current_version"] and t["status"] == "registered"}
            rec = Installed(row["integration_id"], row["integration_type"], row["current_version"], state,
                            TrustClass(row["trust_class"]), snap["manifest"], row["approved_manifest_digest"],
                            row["capability_id"], row["approved_package_digest"], row["approval_binding"] or "", numbers)
            if rec.capability_id and has_executable_tools_raw(rec.manifest):
                d = self.packages_dir / rec.capability_id
                from backend.capability_registry.package import PackageError, package_digest
                try:
                    ok = d.is_dir() and package_digest(d) == rec.package_digest
                except (PackageError, OSError):
                    ok = False
                if not ok and state is not S.BLOCKED:
                    self.store.set_state(rec.integration_id, S.BLOCKED.value, "installed package missing or altered "
                                         "since approval (detected at start)", "integration-manager",
                                         version=rec.version, from_state=state.value)
                    rec.state = S.BLOCKED
                    problems.append(f"{rec.integration_id}: package integrity failed at start")
            self._put(rec)
        return problems

    # ================================================================== discovery (§7) — metadata only
    def _manifest_paths(self) -> list[Path]:
        out = []
        for d in self.config.source_directories:
            root = self.config.path(d)
            if root and root.is_dir():
                out += sorted(p for p in root.glob(f"*/{MANIFEST_FILENAME}") if p.is_file() and not p.is_symlink())
        return out

    def _mcp_definitions(self) -> list[tuple[str, dict]]:
        p = self.config.path(self.config.mcp_definitions)
        if not p or not p.is_file():
            return []
        data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
        return [(f"{p}#servers/{i}", s) for i, s in enumerate(data.get("servers") or [])]

    def discover(self, extra_paths: list[Path] | None = None) -> list[DiscoveryItem]:
        """Read and validate manifests. NEVER imports or executes integration code."""
        items: list[DiscoveryItem] = []
        found: list[tuple[str, IntegrationManifest | None, DiscoveryItem]] = []
        for path in self._manifest_paths() + list(extra_paths or []):
            found.append(self._discover_one(str(path), lambda path=path: load_integration_manifest(
                path, known_permissions=self.known_permissions(), allowed_executables=self._allowed_exe())))
        for src, data in self._mcp_definitions():
            found.append(self._discover_one(src, lambda data=data, src=src: validate_manifest_data(
                data, Path(src), known_permissions=self.known_permissions(), allowed_executables=self._allowed_exe(),
                package=False)))
        by_id: dict[str, list] = {}
        for iid, m, item in found:
            by_id.setdefault(iid, []).append((m, item))
        for iid, group in by_id.items():
            if len(group) > 1:
                for m, item in group:
                    item.valid = False
                    item.problems.append(f"duplicate integration_id '{iid}' in: {', '.join(i.source for _, i in group)}")
            for m, item in group:
                inst = self.get_installed(iid)
                item.installed_version = inst.version if inst else None
                items.append(item)
                if item.valid and m is not None:
                    self.store.add_manifest(m, item.source)
                    row = self.store.integration(iid)
                    if row is None:
                        self.store.upsert_integration({"integration_id": iid, "integration_type": m.type.value,
                                                       "name": m.raw["name"], "provider": m.raw["provider"]["name"],
                                                       "state": S.DISCOVERED.value, "trust_class": "UNTRUSTED"})
                        self.store.set_state(iid, S.DISCOVERED.value, f"discovered at {item.source}", "discovery",
                                             version=m.version)
                    self.store.event(iid, "discovered", "discovery", {"source": item.source, "version": m.version,
                                                                      "manifest_digest": m.manifest_digest,
                                                                      "installed_version": item.installed_version})
                elif not item.valid and not iid.startswith("<"):
                    self.store.event(iid, "discovery_rejected", "discovery", {"source": item.source,
                                                                              "problems": item.problems[:20]})
        return sorted(items, key=lambda i: (i.integration_id, i.source))

    @staticmethod
    def _discover_one(source: str, load) -> tuple[str, IntegrationManifest | None, DiscoveryItem]:
        try:
            m = load()
            return m.integration_id, m, DiscoveryItem(m.integration_id, source, m.version, m.type.value, True,
                                                      manifest_digest=m.manifest_digest)
        except IntegrationManifestError as exc:
            iid = _guess_id(source)
            return iid, None, DiscoveryItem(iid, source, None, None, False, list(exc.problems))

    # ================================================================== validation (§8, §9)
    def _load(self, source: Path | dict, source_label: str | None = None) -> IntegrationManifest:
        if isinstance(source, dict):
            return validate_manifest_data(source, Path(source_label or "<mcp definition>"),
                                          known_permissions=self.known_permissions(),
                                          allowed_executables=self._allowed_exe(), package=False)
        return load_integration_manifest(Path(source), known_permissions=self.known_permissions(),
                                         allowed_executables=self._allowed_exe())

    def validate(self, source: Path | dict, trust_class: TrustClass = TrustClass.TRUSTED,
                 actor: str = "operator") -> ValidationResult:
        label = str(source) if not isinstance(source, dict) else "<mcp definition>"
        try:
            m = self._load(source)
        except IntegrationManifestError as exc:
            iid = _guess_id(label) if not isinstance(source, dict) else str(source.get("integration_id", "<invalid>"))
            security = any(mk in p for p in exc.problems for mk in SECURITY_MARKERS)
            state = S.BLOCKED if security else S.FAILED
            if not iid.startswith("<") and self.store.integration(iid) is not None:
                row = self.store.integration(iid)
                if S(row["state"]) not in INSTALLED_STATES:
                    self.store.set_state(iid, state.value, "validation failed: " + "; ".join(exc.problems)[:1500],
                                         actor, from_state=row["state"], problems=exc.problems)
            if not iid.startswith("<"):
                self.store.event(iid, "validation_failed", actor, {"source": label, "problems": exc.problems[:30],
                                                                   "security": security})
            return ValidationResult(iid, "?", state.value, False, exc.problems)
        iid = m.integration_id
        row = self.store.integration(iid)
        installed = self.get_installed(iid)
        if row is None:
            self.store.upsert_integration({"integration_id": iid, "integration_type": m.type.value, "name": m.raw["name"],
                                           "provider": m.raw["provider"]["name"], "state": S.DISCOVERED.value,
                                           "trust_class": "UNTRUSTED"})
            row = self.store.integration(iid)
        self.store.add_manifest(m, label)
        report = None
        if installed is not None:
            report = compare(installed.manifest, m.raw)
            if installed.integration_type != m.type.value:
                return self._invalid(iid, m, ["integration_type cannot change across versions"], actor)
        action = "integration.upgrade" if installed is not None else "integration.install"
        binding = approval_binding(m, trust_class, report)
        if installed is None:
            st = S(row["state"])
            if st is not S.VALIDATING:
                self._transition(iid, st, S.VALIDATING, "validation started", actor, m.version)
            self._transition(iid, S.VALIDATING, S.AWAITING_APPROVAL, "manifest valid; operator approval required",
                             actor, m.version)
        self.store.event(iid, "validated", actor, {"version": m.version, "manifest_digest": m.manifest_digest,
                                                   "package_digest": m.package_digest, "trust_class": trust_class.value,
                                                   "approval_binding": binding, "upgrade_report": report})
        return ValidationResult(iid, m.version, (S.AWAITING_APPROVAL if installed is None else S(row["state"])).value,
                                True, [], m.manifest_digest, m.package_digest, action, f"{iid}@{m.version}", binding,
                                report)

    def _invalid(self, iid, m, problems, actor):
        self.store.event(iid, "validation_failed", actor, {"problems": problems})
        return ValidationResult(iid, m.version, "FAILED", False, problems)

    def _transition(self, iid: str, frm: IntegrationState, to: IntegrationState, reason: str, actor: str,
                    version: str | None, **fields) -> None:
        if to not in TRANSITIONS[frm]:
            raise IntegrationError(f"invalid lifecycle transition {frm.value} -> {to.value}", status="REFUSED")
        self.store.set_state(iid, to.value, reason, actor, version=version, from_state=frm.value, **fields)

    def _verify_auth(self, auth: OperatorAuthorization | None, action: str, subject: str, payload: str) -> str:
        try:
            return self.authorizer.verify(auth, action, subject, payload)
        except AuthorizationError as exc:
            raise IntegrationError(f"operator authorisation refused: {exc}", status="REFUSED") from exc

    # ================================================================== installation (§10) — transactional
    def install(self, source: Path | dict, auth: OperatorAuthorization | None, *,
                trust_class: TrustClass = TrustClass.TRUSTED, actor: str = "operator") -> dict:
        if trust_class in (TrustClass.DISABLED,):
            raise IntegrationError("an integration cannot be installed with trust class DISABLED", status="REFUSED")
        m = self._load(source)      # re-validated NOW from the bytes on disk (no reliance on an earlier check)
        iid = m.integration_id
        if self.get_installed(iid) is not None:
            raise IntegrationError(f"{iid} is installed; use upgrade()", status="REFUSED")
        binding = approval_binding(m, trust_class, None)
        operator = self._verify_auth(auth, "integration.install", f"{iid}@{m.version}", binding)
        row = self.store.integration(iid)
        if row is None or S(row["state"]) is not S.AWAITING_APPROVAL:
            raise IntegrationError(f"{iid} must be validated (AWAITING_APPROVAL) before installation; state is "
                                   f"{row['state'] if row else 'unknown'}", status="REFUSED")
        cap_id = capability_id_for(iid)
        clash = self.registry.get(cap_id)
        if clash is not None and self.by_capability(cap_id) is None:
            return self._fail(iid, m, S.AWAITING_APPROVAL, [f"capability id {cap_id} already registered from "
                                                             f"{clash.source} (collision)"], actor)
        for dep in (m.raw.get("dependencies") or {}).get("integrations") or []:
            if self.get_installed(dep) is None:
                return self._fail(iid, m, S.AWAITING_APPROVAL, [f"dependency integration '{dep}' is not installed"],
                                  actor)
        self.store.add_version(iid, m.version, m.manifest_digest, m.package_digest, trust_class.value, binding, operator)
        self.store.event(iid, "install_approved", operator, {"version": m.version, "binding": binding,
                                                             "trust_class": trust_class.value})
        return self._install_version(m, trust_class, binding, actor=operator, from_state=S.AWAITING_APPROVAL)

    def _install_version(self, m: IntegrationManifest, trust_class: TrustClass, binding: str, *, actor: str,
                         from_state: IntegrationState, previous: Installed | None = None,
                         upgrade_report: dict | None = None) -> dict:
        iid, cap_id = m.integration_id, capability_id_for(m.integration_id)
        target = self.packages_dir / cap_id
        backup = self.backups_dir / f"{cap_id}--{uuid.uuid4().hex[:6]}"
        staging = self.staging_dir / f"{cap_id}-{uuid.uuid4().hex[:8]}"
        moved = backed_up = False
        state = from_state
        executable = has_executable_tools(m)
        try:
            self._transition(iid, state, S.INSTALLING, f"installing {m.version}", actor, m.version)
            state = S.INSTALLING
            self._fault("installing", iid)
            pkg = None
            if executable:
                deps = [capability_id_for(d) for d in (m.raw.get("dependencies") or {}).get("integrations") or []
                        if (self.get_installed(d) and self.get_installed(d).capability_id)]
                pkg = build_package(m, staging, request_timeout_s=self.config.mcp_request_timeout_s,
                                    startup_timeout_s=self.config.mcp_startup_timeout_s, dependency_capabilities=deps)
            self._transition(iid, state, S.REGISTERING, "registering capabilities and tools", actor, m.version)
            state = S.REGISTERING
            numbers: dict[str, str] = {}
            if executable:
                wanted = [(f"{cap_id}:{t['name']}", f"{int(m.requirement.split('-')[1]):03d}") for t in m.tools]
                from backend.capability_registry.numbering import load_numbers
                base = load_numbers(self.catalogue.numbers_file)
                assigned = self.store.assign_numbers(iid, wanted, set(base), AUTO_NUMBER_BAND)
                for n, e in base.items():         # an operator-file number always wins for its tool
                    if e.tool in assigned:
                        assigned[e.tool] = n
                numbers = {t["name"]: assigned[f"{cap_id}:{t['name']}"] for t in m.tools}
                from .packaging import tool_output_schema
                schema_hashes = {t["name"]: canonical_hash([t["input_schema"], tool_output_schema(m, t),
                                                            t["error_schema"]]) for t in m.tools}
                creds = {c["name"]: credential_ref(iid, c["name"]) for c in m.raw.get("credential_requirements") or []}
                if previous is not None:
                    self.store.set_tool_status(iid, previous.version, "superseded", only="registered")
                self.store.record_registration(m, cap_id, numbers, schema_hashes, creds)
                self._fault("registering", iid)
                if target.exists():
                    os.replace(target, backup)
                    backed_up = True
                os.replace(pkg.directory, target)
                moved = True
            rec = Installed(iid, m.type.value, m.version, S.VERIFYING, trust_class, m.raw, m.manifest_digest,
                            cap_id if executable else None, pkg.package_digest if pkg else None, binding, numbers)
            self._transition(iid, state, S.VERIFYING, "independent verification", actor, m.version)
            state = S.VERIFYING
            self._put(rec)
            if executable:
                self.registry.directories = _with_dir(self.registry.directories, self.packages_dir)
                self.registry.discover()
                self.catalogue.load()
            self._fault("verifying", iid)
            pre = assess(rec, network=self.network, isolation=self.isolation, broker=self.broker,
                         lookup=self.get_installed, allow_states=frozenset({S.VERIFYING}))
            report = self.verifier.verify(rec, package_dir=target if executable else None,
                                          expected_block=bool(pre.blocked))
            extra = self._type_specific_install(m, rec, actor)
            problems = report.problems + extra.get("problems", [])
            if problems:
                raise IntegrationError("verification failed", problems=problems)
            rec.state = S.INSTALLED
            self._transition(iid, S.VERIFYING, S.INSTALLED, "installed and independently verified", actor, m.version,
                             current_version=m.version, trust_class=trust_class.value, capability_id=rec.capability_id,
                             approved_manifest_digest=m.manifest_digest, approved_package_digest=rec.package_digest,
                             approval_binding=binding, installed_at=_now(), problems=[])
            if executable:
                self.registry.refresh_dependencies()
                self.catalogue.refresh_availability()
            if backed_up:
                shutil.rmtree(backup, ignore_errors=True)
            self.store.event(iid, "installed" if previous is None else "upgraded", actor,
                             {"version": m.version, "tool_numbers": numbers, "verification": report.as_dict(),
                              "upgrade_report": upgrade_report, **{k: v for k, v in extra.items() if k != "problems"}},
                             version=m.version)
            return {"status": "INSTALLED", "integration_id": iid, "version": m.version, "tool_numbers": numbers,
                    "capability_id": rec.capability_id, "verification": report.as_dict(), **extra}
        except Exception as exc:  # noqa: BLE001 — every failure rolls back; nothing half-registered survives
            problems = exc.problems if isinstance(exc, IntegrationError) else [f"{type(exc).__name__}: {exc}"[:500]]
            self._rollback(m, staging, target, backup, moved, backed_up, previous)
            if previous is not None:
                self.store.event(iid, "upgrade_failed_rolled_back", actor, {"version": m.version,
                                                                             "problems": problems[:20]},
                                 version=m.version)
                self.store.set_state(iid, previous.state.value, f"upgrade to {m.version} failed and was rolled back: "
                                     + "; ".join(problems)[:1000], actor, version=previous.version,
                                     from_state=state.value, problems=problems)
                return {"status": "FAILED", "integration_id": iid, "version": m.version, "problems": problems,
                        "rolled_back_to": previous.version}
            return self._fail(iid, m, state, problems, actor, rolled_back=True)

    def _rollback(self, m, staging, target, backup, moved, backed_up, previous):
        iid = m.integration_id
        shutil.rmtree(staging, ignore_errors=True)
        if moved:
            shutil.rmtree(target, ignore_errors=True)
        if backed_up:
            os.replace(backup, target)
        self.store.set_tool_status(iid, m.version, "rolled_back", only="registered")
        if previous is not None:
            self.store.set_tool_status(iid, previous.version, "registered", only="superseded")
            self._put(previous)
        else:
            self._drop(iid)
        if self.packages_dir in [Path(d) for d in self.registry.directories]:
            self.registry.discover()
            self.catalogue.load()
        self.store.event(iid, "rolled_back", "integration-manager", {"version": m.version,
                                                                      "restored_previous": previous.version if previous
                                                                      else None}, version=m.version)

    def _fail(self, iid, m, from_state, problems, actor, rolled_back=False):
        security = any("malicious" in p or any(mk in p for mk in SECURITY_MARKERS) for p in problems)
        to = S.BLOCKED if security else S.FAILED
        try:
            self._transition(iid, from_state, to, "; ".join(problems)[:1500], actor, m.version, problems=problems)
        except IntegrationError:
            self.store.set_state(iid, to.value, "; ".join(problems)[:1500], actor, version=m.version,
                                 from_state=from_state.value, problems=problems)
        self.store.event(iid, "install_blocked" if security else "install_failed", actor,
                         {"version": m.version, "problems": problems[:30], "rolled_back": rolled_back},
                         version=m.version)
        return {"status": to.value, "integration_id": iid, "version": m.version, "problems": problems,
                "rolled_back": rolled_back}

    def _type_specific_install(self, m: IntegrationManifest, rec: Installed, actor: str) -> dict:
        out: dict = {}
        if m.type is IntegrationType.MCP_SERVER:
            out.update(self._verify_mcp(m, rec))
        if m.type is IntegrationType.SKILL:
            content, problems = build_skill_content(m.raw, m.directory, self.catalogue)
            if problems:
                return {"problems": problems}
            self.store.put_skill_version(content.skill_id, m.integration_id, content.version, content.content_digest,
                                         content.body())
            self.store.set_skill(content.skill_id, m.integration_id, content.version, "DISABLED")
            out["skill"] = {"skill_id": content.skill_id, "version": content.version,
                            "content_digest": content.content_digest, "tool_references": content.tool_references}
        if m.type in (IntegrationType.PLUGIN, IntegrationType.CUSTOM_PROVIDER):
            contributes = (m.raw.get("plugin") or {}).get("contributes") or {}
            self.store.put_plugin_version(m.integration_id, m.version, rec.package_digest or m.package_digest or "",
                                          {**contributes, "tools": [t["name"] for t in m.tools]},
                                          needs_isolation(rec), "INSTALLED")
            out["plugin"] = {"isolation_required": needs_isolation(rec),
                             "isolation": self.isolation.report().__dict__ if needs_isolation(rec) else None}
        return out

    def _verify_mcp(self, m: IntegrationManifest, rec: Installed) -> dict:
        """Capability negotiation + discovery against the APPROVED manifest. Runs the server only after approval."""
        iid = m.integration_id
        endpoint = {k: v for k, v in m.raw["mcp"].items() if k in ("transport", "command", "url")}
        self.store.upsert_mcp_server(iid, m.raw["mcp"]["transport"], endpoint, "STARTING")
        offline = self.network.integration_offline(m.network_hosts)
        if offline:
            self.store.upsert_mcp_server(iid, m.raw["mcp"]["transport"], endpoint, "STOPPED",
                                         last_error="; ".join(offline)[:500])
            return {"mcp": {"negotiation": "deferred", "reason": offline}}
        try:
            info = mcp_client.negotiate_and_discover(m.raw, m.directory if m.files else None, self.network,
                                                     request_timeout_s=self.config.mcp_request_timeout_s,
                                                     startup_timeout_s=self.config.mcp_startup_timeout_s)
        except mcp_client.McpError as exc:
            self.store.upsert_mcp_server(iid, m.raw["mcp"]["transport"], endpoint, "UNHEALTHY",
                                         last_error=f"{exc.kind}: {exc}"[:500])
            return {"problems": [f"MCP negotiation failed ({exc.kind}): {exc}"[:500]]}
        chk = check_server(m.tools, info["tools"], info["resources"], info["prompts"])
        for t in chk.tools:
            self.store.add_mcp_capability(iid, m.version, "tool", t.name, t.schema_hash, {**t.metadata,
                                          "problems": t.problems, "declared": t.declared}, t.accepted)
        for r in chk.resources:
            self.store.add_mcp_capability(iid, m.version, "resource", r["uri"][:200], "", r, False)
        for p in chk.prompts:
            self.store.add_mcp_capability(iid, m.version, "prompt", p["name"], "", p, False)
        lifecycle = "BLOCKED" if chk.malicious else "HEALTHY" if chk.ok and not chk.undeclared else \
            "DEGRADED" if chk.ok else "UNHEALTHY"
        self.store.upsert_mcp_server(iid, m.raw["mcp"]["transport"], endpoint, lifecycle, info["protocol_version"],
                                     {"server_info": info["server_info"], "capabilities": info["server_capabilities"],
                                      "instructions_ignored": info["instructions_ignored"]},
                                     last_error=None if chk.ok else str(chk.summary())[:500])
        if chk.undeclared:
            self.store.event(iid, "mcp_undeclared_tools_ignored", "mcp-verifier", {"tools": chk.undeclared[:50]},
                             version=m.version)
        problems = []
        if chk.malicious:
            problems.append(f"MCP server metadata is malicious: {chk.malicious[:5]}")
        if chk.missing:
            problems.append(f"MCP server does not offer declared tools {chk.missing}")
        for t in chk.tools:
            if t.declared and not t.accepted:
                problems.append(f"MCP tool {t.name}: {t.problems}")
        return {"mcp": {"protocol_version": info["protocol_version"], "server_info": info["server_info"],
                        "check": chk.summary()}, "problems": problems}

    # ================================================================== enable / disable / trust (§9, §11)
    def enable(self, integration_id: str, auth: OperatorAuthorization | None, actor: str = "operator") -> dict:
        rec = self._require(integration_id)
        payload = canonical_hash({"integration_id": integration_id, "version": rec.version, "binding": rec.binding})
        operator = self._verify_auth(auth, "integration.enable", integration_id, payload)
        if rec.state not in (S.INSTALLED, S.DISABLED):
            raise IntegrationError(f"cannot enable from {rec.state.value}", status="REFUSED")
        self._transition(integration_id, rec.state, S.ENABLED, "enabled by operator", operator, rec.version,
                         disabled_by_admin=False)
        rec.state = S.ENABLED
        self._after_state_change(rec, "enabled", operator)
        return {"status": "ENABLED", "integration_id": integration_id, "version": rec.version}

    def disable(self, integration_id: str, actor: str, reason: str = "disabled by administrator") -> dict:
        rec = self._require(integration_id)
        if rec.state is S.DISABLED:
            return {"status": "DISABLED", "integration_id": integration_id}
        self._transition(integration_id, rec.state, S.DISABLED, reason, actor, rec.version, disabled_by_admin=True)
        rec.state = S.DISABLED
        self._after_state_change(rec, "disabled", actor)
        return {"status": "DISABLED", "integration_id": integration_id}

    def set_trust(self, integration_id: str, trust_class: TrustClass, auth: OperatorAuthorization | None) -> dict:
        rec = self._require(integration_id)
        payload = canonical_hash({"integration_id": integration_id, "version": rec.version, "trust_class": trust_class.value})
        operator = self._verify_auth(auth, "integration.trust", integration_id, payload)
        old = rec.trust_class
        rec.trust_class = trust_class
        self.store.set_state(integration_id, rec.state.value, f"trust {old.value} -> {trust_class.value}", operator,
                             version=rec.version, from_state=rec.state.value, trust_class=trust_class.value)
        self._after_state_change(rec, "trust_changed", operator, {"from": old.value, "to": trust_class.value})
        return {"integration_id": integration_id, "trust_class": trust_class.value}

    def _after_state_change(self, rec: Installed, event: str, actor: str, details: dict | None = None) -> None:
        if rec.capability_id:
            if rec.state is S.ENABLED:
                self.registry.discover()       # re-probe now that it may execute (probe is deferred while blocked)
                self.catalogue.load()
            else:
                self.registry.refresh_dependencies()
                self.catalogue.refresh_availability()
        if rec.integration_type == IntegrationType.SKILL.value:
            sk = rec.manifest["skill"]["skill_id"]
            self.store.set_skill(sk, rec.integration_id, rec.version,
                                 "ENABLED" if rec.state is S.ENABLED else "DISABLED")
        if rec.integration_type in (IntegrationType.PLUGIN.value, IntegrationType.CUSTOM_PROVIDER.value):
            self.store.set_plugin_state(rec.integration_id, {S.ENABLED: "ENABLED", S.DISABLED: "DISABLED",
                                                             S.BLOCKED: "BLOCKED"}.get(rec.state, "INSTALLED"))
        self._health_cache.pop(rec.integration_id, None)
        self.store.event(rec.integration_id, event, actor, details or {"state": rec.state.value}, version=rec.version)

    def _require(self, integration_id: str) -> Installed:
        rec = self.get_installed(integration_id)
        if rec is None:
            raise IntegrationError(f"integration '{integration_id}' is not installed", status="NOT_FOUND")
        return rec

    # ================================================================== upgrade (§19, §38, §39)
    def upgrade(self, source: Path | dict, auth: OperatorAuthorization | None, *, trust_class: TrustClass | None = None,
                actor: str = "operator") -> dict:
        m = self._load(source)
        iid = m.integration_id
        prev = self._require(iid)
        trust = trust_class or prev.trust_class
        if m.version == prev.version and m.manifest_digest == prev.manifest_digest:
            return {"status": "UNCHANGED", "integration_id": iid, "version": m.version}
        report = compare(prev.manifest, m.raw)
        binding = approval_binding(m, trust, report)
        operator = self._verify_auth(auth, "integration.upgrade", f"{iid}@{m.version}", binding)
        active = self.active_tasks(list(prev.numbers.values()))
        if active:
            self.store.event(iid, "upgrade_deferred", operator, {"version": m.version, "active_tasks": [
                f"{a['objective_id']}/{a['task_id']}" for a in active][:50]}, version=prev.version)
            return {"status": "DEFERRED", "integration_id": iid, "version": m.version, "reason":
                    "active tasks are pinned to the installed version; upgrade after they finish",
                    "active_tasks": [{"objective_id": a["objective_id"], "task_id": a["task_id"], "state": a["state"]}
                                     for a in active], "upgrade_report": report}
        self.store.add_manifest(m, str(source) if not isinstance(source, dict) else "<mcp definition>")
        self.store.add_version(iid, m.version, m.manifest_digest, m.package_digest, trust.value, binding, operator,
                               report)
        prev_state = prev.state
        self._transition(iid, prev_state, S.VALIDATING, f"upgrade {prev.version} -> {m.version} validated", operator,
                         m.version)
        self._transition(iid, S.VALIDATING, S.AWAITING_APPROVAL, "upgrade approved", operator, m.version)
        prev_copy = Installed(**{**prev.__dict__})
        prev_copy.state = prev_state
        prev.state = S.VALIDATING            # no allocation/execution of the old version while it is being replaced
        res = self._install_version(m, trust, binding, actor=operator, from_state=S.AWAITING_APPROVAL,
                                    previous=prev_copy, upgrade_report=report)
        if res["status"] == "INSTALLED":
            for name in report["tools_removed"]:
                self.store.event(iid, "tool_removed_by_upgrade", operator, {"tool": name,
                                                                             "number": prev.numbers.get(name)})
            if prev_state is S.ENABLED:
                # The operator's signature covered this exact upgrade (diff hash included in the binding), so the
                # previous ENABLED state is restored; nothing else (trust, pools, approvals) changes.
                new = self._require(iid)
                self._transition(iid, S.INSTALLED, S.ENABLED, "re-enabled after the approved upgrade", operator,
                                 m.version, disabled_by_admin=False)
                new.state = S.ENABLED
                self._after_state_change(new, "enabled", operator, {"after_upgrade_from": prev.version})
                res["state"] = "ENABLED"
        res["upgrade_report"] = report
        return res

    # ================================================================== removal (§40)
    def remove(self, integration_id: str, actor: str, reason: str = "removed by operator") -> dict:
        rec = self._require(integration_id)
        if rec.state is not S.REMOVING:
            self._transition(integration_id, rec.state, S.REMOVING, reason, actor, rec.version)
            rec.state = S.REMOVING
            self._after_state_change(rec, "removal_started", actor)
        active = self.active_tasks(list(rec.numbers.values()))
        if active:
            return {"status": "PENDING_ACTIVE_TASKS", "integration_id": integration_id,
                    "active_tasks": [{"objective_id": a["objective_id"], "task_id": a["task_id"], "state": a["state"]}
                                     for a in active],
                    "detail": "no new allocations or executions are possible; call remove() again once these tasks end"}
        if rec.capability_id:
            shutil.rmtree(self.packages_dir / rec.capability_id, ignore_errors=True)
        self.store.set_tool_status(integration_id, rec.version, "removed", only="registered")
        self._transition(integration_id, S.REMOVING, S.REMOVED, reason, actor, rec.version, capability_id=None,
                         approved_package_digest=None)
        if rec.integration_type == IntegrationType.SKILL.value:
            self.store.set_skill(rec.manifest["skill"]["skill_id"], integration_id, rec.version, "REMOVED")
        if rec.integration_type in (IntegrationType.PLUGIN.value, IntegrationType.CUSTOM_PROVIDER.value):
            self.store.set_plugin_state(integration_id, "REMOVED")
        self._drop(integration_id)
        if rec.capability_id:
            self.registry.discover()
            self.catalogue.load()
        self.store.event(integration_id, "removed", actor, {"version": rec.version, "retained": [
            "manifests", "versions", "tool records and permanent numbers", "events", "task pins"]}, version=rec.version)
        return {"status": "REMOVED", "integration_id": integration_id}

    # ================================================================== health (§17, §34) — dynamic
    def health(self, integration_id: str, *, max_age_s: float | None = None) -> dict:
        max_age = self.config.health_max_age_s if max_age_s is None else max_age_s
        cached = self._health_cache.get(integration_id)
        if cached and max_age > 0 and time.time() - cached[0] < max_age:
            return cached[1]
        rec = self.get_installed(integration_id)
        if rec is None:
            row = self.store.integration(integration_id)
            if row is None:
                raise IntegrationError(f"unknown integration '{integration_id}'", status="NOT_FOUND")
            st = HealthStatus.BLOCKED if row["state"] in ("BLOCKED", "FAILED") else HealthStatus.STOPPED
            out = {"integration_id": integration_id, "status": st.value, "state": row["state"],
                   "reasons": [f"not installed ({row['state']})"], "checked_at": _now()}
            self.store.add_health(integration_id, st.value, out)
            return out
        a: Assessment = assess(rec, network=self.network, isolation=self.isolation, broker=self.broker,
                               lookup=self.get_installed)
        status, reasons, detail = a.status(), a.reasons(), {}
        if status is None and rec.capability_id:
            from backend.capability_registry.package import PackageError, package_digest
            try:
                intact = package_digest(self.packages_dir / rec.capability_id) == rec.package_digest
            except (PackageError, OSError):
                intact = False
            if not intact:
                status, reasons = HealthStatus.BLOCKED, ["installed package bytes differ from the approved package"]
        if status is None:
            if rec.capability_id:
                c = self.registry.get(rec.capability_id)
                if c is not None and c.probe_problems is None:
                    self.registry.discover()    # first time executable: run the isolated probe before reporting
                    c = self.registry.get(rec.capability_id)
                if c is not None:
                    c.health_at = 0.0           # force a live health check now (never a startup snapshot)
                    self.registry.refresh_dependencies()
                    c = self.registry.get(rec.capability_id)
                detail = {"registry_state": c.state.value if c else "absent", "health": c.health_detail if c else None}
                if c is None or c.state.value in ("integrity_failed", "untrusted", "invalid"):
                    status, reasons = HealthStatus.BLOCKED, (c.problems if c else ["not registered"])
                elif c.state.value == "unhealthy":
                    status, reasons = HealthStatus.UNHEALTHY, c.problems
                elif c.state.value == "dependency_missing":
                    status, reasons = HealthStatus.DEGRADED, c.problems
                elif c.state.value == "available":
                    hd = c.health_detail if isinstance(c.health_detail, dict) else {}
                    status = HealthStatus.DEGRADED if hd.get("undeclared_offered") else HealthStatus.HEALTHY
                    reasons = ["server offers undeclared tools (ignored)"] if hd.get("undeclared_offered") else []
                else:
                    status, reasons = HealthStatus.STOPPED, [c.state.value]
            elif rec.integration_type == IntegrationType.SKILL.value:
                refs = rec.manifest["skill"]["tool_references"]
                from .skills import resolve_reference
                unresolved = [r for r in refs if resolve_reference(r, self.catalogue) is None or
                              not self.catalogue.get(resolve_reference(r, self.catalogue)).available]
                status = HealthStatus.DEGRADED if unresolved else HealthStatus.HEALTHY
                reasons = [f"referenced tool unavailable: {r}" for r in unresolved]
            else:
                status = HealthStatus.HEALTHY
        if rec.integration_type == IntegrationType.MCP_SERVER.value:
            lifecycle = {HealthStatus.HEALTHY: "HEALTHY", HealthStatus.DEGRADED: "DEGRADED",
                         HealthStatus.UNHEALTHY: "UNHEALTHY", HealthStatus.BLOCKED: "BLOCKED"}.get(status, "STOPPED")
            endpoint = {k: v for k, v in rec.manifest["mcp"].items() if k in ("transport", "command", "url")}
            self.store.upsert_mcp_server(rec.integration_id, rec.manifest["mcp"]["transport"], endpoint, lifecycle,
                                         last_error="; ".join(reasons)[:500] or None)
        out = {"integration_id": integration_id, "version": rec.version, "status": status.value,
               "state": rec.state.value, "trust_class": rec.trust_class.value, "reasons": reasons,
               "detail": detail, "checked_at": _now()}
        self.store.add_health(integration_id, status.value, out)
        self._health_cache[integration_id] = (time.time(), out)
        return out

    def tool_number_for_proposal(self, ctx, proposal_id):   # compatibility helper (unused by the auditor wrapper)
        return None


def has_executable_tools_raw(raw: dict) -> bool:
    return bool(raw.get("tools"))


def _with_dir(dirs, d: Path) -> list[Path]:
    dirs = [Path(x) for x in dirs]
    return dirs if d in dirs else dirs + [d]


def _guess_id(source: str) -> str:
    try:
        p = Path(source.split("#")[0])
        data = yaml.safe_load(p.read_text(encoding="utf-8")) if p.is_file() else None
        if isinstance(data, dict) and isinstance(data.get("integration_id"), str) and \
                not injection_findings(data["integration_id"]):
            import re
            if re.fullmatch(r"[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)+", data["integration_id"]):
                return data["integration_id"]
    except Exception:  # noqa: BLE001
        pass
    return f"<invalid:{Path(source).parent.name}>"


def _now() -> str:
    from backend.contracts import iso_utc
    return iso_utc()
