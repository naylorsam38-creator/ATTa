"""Capability manifest parsing and validation — WITHOUT importing any capability code (correction 4).

Handler modules are only located (must exist inside the package, no symlinks). Importing them, and checking that
each handler is a callable with the right signature, happens only inside the isolated runtime (probe mode)."""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

import yaml
from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError

from backend.contracts import RiskClass

MANIFEST_FILENAME = "capability.yaml"
_META = Draft202012Validator(json.loads((Path(__file__).with_name("manifest.schema.json")).read_text()))
# Handler signatures: name -> number of required positional parameters (checked in the isolated probe).
HANDLER_ARITY = {"execute": 2, "healthcheck": 0, "verify": 2, "reconcile": 2}


class ManifestError(Exception):
    def __init__(self, source: str, problems: list[str]):
        super().__init__(f"{source}: " + "; ".join(problems))
        self.source = source
        self.problems = problems


@dataclass
class CapabilityManifest:
    id: str
    version: str
    description: str
    requirement: str | None
    input_schema: dict
    output_schema: dict
    permissions: frozenset[str]
    risk: RiskClass
    capability_dependencies: tuple[str, ...]
    service_dependencies: tuple[str, ...]
    python_dependencies: tuple[str, ...]
    timeout_s: float
    enabled: bool
    source: Path
    handler_refs: dict[str, str]
    digest: str = ""
    handlers: dict = field(default_factory=dict)   # never populated in the API process (kept for compatibility)

    @property
    def directory(self) -> Path:
        return self.source.parent


def _module_file(cap_dir: Path, module: str) -> Path | None:
    rel = Path(*module.split("."))
    for c in (cap_dir / rel.with_suffix(".py"), cap_dir / rel / "__init__.py"):
        if c.is_symlink():
            raise ValueError(f"handler module '{module}' is a symlink and resolves outside the capability directory")
        if c.is_file():
            if cap_dir.resolve() not in c.resolve().parents:
                raise ValueError(f"handler module '{module}' resolves outside the capability directory")
            return c
    return None


def load_manifest_file(path: Path, max_timeout_s: float = 3600.0) -> CapabilityManifest:
    """Parse + validate a manifest and locate (not import) its handlers. Raises ManifestError listing problems."""
    import hashlib
    src = str(path)
    try:
        raw_bytes = path.read_bytes()
        data = yaml.safe_load(raw_bytes)
    except (OSError, yaml.YAMLError) as exc:
        raise ManifestError(src, [f"unreadable manifest: {exc}"]) from exc
    if not isinstance(data, dict):
        raise ManifestError(src, ["manifest must be a mapping"])
    problems = [f"{'/'.join(str(p) for p in e.absolute_path) or '<root>'}: {e.message}"
                for e in sorted(_META.iter_errors(data), key=lambda e: list(e.absolute_path))]
    for key in ("input_schema", "output_schema"):
        if isinstance(data.get(key), dict):
            try:
                Draft202012Validator.check_schema(data[key])
            except SchemaError as exc:
                problems.append(f"{key} is not a valid JSON Schema: {exc.message}")
    if isinstance(data.get("timeout_s"), (int, float)) and data["timeout_s"] > max_timeout_s:
        problems.append(f"timeout_s {data['timeout_s']} exceeds the configured maximum {max_timeout_s}")
    if problems:
        raise ManifestError(src, problems)
    for kind, ref in data["handlers"].items():
        module = ref.split(":")[0]
        try:
            if _module_file(path.parent, module) is None:
                problems.append(f"{kind} handler '{ref}': module '{module}' not found inside {path.parent.name}/")
        except ValueError as exc:
            problems.append(f"{kind} handler '{ref}': {exc}")
    if problems:
        raise ManifestError(src, problems)
    deps = data.get("dependencies") or {}
    return CapabilityManifest(
        id=data["id"], version=data["version"], description=data["description"],
        requirement=data.get("requirement"), input_schema=data["input_schema"], output_schema=data["output_schema"],
        permissions=frozenset(data["permissions"]), risk=RiskClass(data["risk"]),
        capability_dependencies=tuple(deps.get("capabilities", [])),
        service_dependencies=tuple(deps.get("services", [])), python_dependencies=tuple(deps.get("python", [])),
        timeout_s=float(data["timeout_s"]), enabled=bool(data["enabled"]), source=path,
        handler_refs=dict(data["handlers"]), digest=hashlib.sha256(raw_bytes).hexdigest())


@dataclass(frozen=True)
class ToolSpec:
    """One executable tool of a capability (Stage 01A §5). `string_id` = '<capability id>:<tool name>'."""
    string_id: str
    capability_id: str
    name: str
    description: str
    task_types: tuple[str, ...]
    operations: tuple[str, ...]
    handler: str
    verify: str
    input_schema: dict
    output_schema: dict
    permissions: frozenset[str]
    risk: RiskClass
    resource_types: tuple[str, ...]
    path_arguments: tuple[str, ...]
    url_arguments: tuple[str, ...]
    timeout_s: float
    resource_limits: dict
    cancellable: bool
    idempotency: str
    version: str


def tools_of(m: CapabilityManifest, raw: dict | None = None) -> list[ToolSpec]:
    """Tools declared by a manifest; a manifest without `tools` exposes one implicit tool named 'default'."""
    if raw is None:
        raw = yaml.safe_load(m.source.read_bytes())
    declared = raw.get("tools")
    if not declared:
        declared = [{"name": "default", "description": m.description, "task_types": [m.id], "operations": ["execute"],
                     "handler": m.handler_refs["execute"], "verify": m.handler_refs["verify"],
                     "input_schema": m.input_schema, "output_schema": m.output_schema,
                     "permissions": sorted(m.permissions), "risk": m.risk.value, "timeout_s": m.timeout_s,
                     "cancellable": True, "idempotency": "non_idempotent"}]
    out, names = [], set()
    for t in declared:
        if t["name"] in names:
            raise ManifestError(str(m.source), [f"duplicate tool name '{t['name']}'"])
        names.add(t["name"])
        extra = set(t["permissions"]) - set(m.permissions)
        if extra:
            raise ManifestError(str(m.source), [f"tool '{t['name']}' requires permissions the capability does not "
                                                f"declare: {sorted(extra)}"])
        for key in ("input_schema", "output_schema"):
            try:
                Draft202012Validator.check_schema(t[key])
            except SchemaError as exc:
                raise ManifestError(str(m.source), [f"tool '{t['name']}' {key} invalid: {exc.message}"]) from exc
        for ref in (t["handler"], t["verify"]):
            if _module_file(m.directory, ref.split(":")[0]) is None:
                raise ManifestError(str(m.source), [f"tool '{t['name']}' handler module for '{ref}' not found"])
        out.append(ToolSpec(
            string_id=f"{m.id}:{t['name']}", capability_id=m.id, name=t["name"], description=t["description"],
            task_types=tuple(t.get("task_types") or [m.id]), operations=tuple(t["operations"]), handler=t["handler"],
            verify=t["verify"], input_schema=t["input_schema"], output_schema=t["output_schema"],
            permissions=frozenset(t["permissions"]), risk=RiskClass(t["risk"]),
            resource_types=tuple(t.get("resource_types") or []), path_arguments=tuple(t.get("path_arguments") or []),
            url_arguments=tuple(t.get("url_arguments") or []), timeout_s=float(t["timeout_s"]),
            resource_limits=dict(t.get("resource_limits") or {}), cancellable=bool(t["cancellable"]),
            idempotency=t["idempotency"], version=m.version))
    return out
