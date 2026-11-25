"""Capability manifest model, validation and handler resolution."""
from __future__ import annotations

import hashlib
import importlib.util
import inspect
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

import yaml
from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError

from backend.contracts import RiskClass

MANIFEST_FILENAME = "capability.yaml"
_META = Draft202012Validator(json.loads((Path(__file__).with_name("manifest.schema.json")).read_text()))
# Handler signatures: name -> number of required positional parameters.
HANDLER_ARITY = {"execute": 2, "healthcheck": 0, "verify": 2}


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
    handlers: dict[str, Callable[..., Any]] = field(default_factory=dict)
    digest: str = ""


def _load_module(cap_dir: Path, module: str, unique: str):
    rel = Path(*module.split("."))
    candidates = [cap_dir / rel.with_suffix(".py"), cap_dir / rel / "__init__.py"]
    path = next((c for c in candidates if c.is_file()), None)
    if path is None:
        raise ImportError(f"handler module '{module}' not found inside {cap_dir.name}/")
    resolved = path.resolve()
    if cap_dir.resolve() not in resolved.parents:
        raise ImportError(f"handler module '{module}' resolves outside the capability directory")
    mod_name = f"atta_capability_{unique}_{module.replace('.', '_')}"
    spec = importlib.util.spec_from_file_location(mod_name, resolved)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[mod_name] = mod
    try:
        spec.loader.exec_module(mod)  # type: ignore[union-attr]
    except BaseException:
        sys.modules.pop(mod_name, None)
        raise
    return mod


def _check_arity(fn: Callable, required: int) -> str | None:
    try:
        sig = inspect.signature(fn)
    except (TypeError, ValueError):
        return "signature cannot be inspected"
    params = [p for p in sig.parameters.values() if p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD)]
    has_var = any(p.kind is p.VAR_POSITIONAL for p in sig.parameters.values())
    req = [p for p in params if p.default is p.empty]
    if len(req) > required or (len(params) < required and not has_var):
        return f"must accept exactly {required} positional argument(s)"
    return None


def load_manifest_file(path: Path, max_timeout_s: float = 3600.0) -> CapabilityManifest:
    """Parse + validate a manifest and resolve its handlers. Raises ManifestError listing every problem."""
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

    deps = data.get("dependencies") or {}
    manifest = CapabilityManifest(
        id=data["id"], version=data["version"], description=data["description"],
        requirement=data.get("requirement"), input_schema=data["input_schema"], output_schema=data["output_schema"],
        permissions=frozenset(data["permissions"]), risk=RiskClass(data["risk"]),
        capability_dependencies=tuple(deps.get("capabilities", [])),
        service_dependencies=tuple(deps.get("services", [])), python_dependencies=tuple(deps.get("python", [])),
        timeout_s=float(data["timeout_s"]), enabled=bool(data["enabled"]), source=path,
        handler_refs=dict(data["handlers"]), digest=hashlib.sha256(raw_bytes).hexdigest(),
    )
    unique = hashlib.sha256(str(path.resolve()).encode()).hexdigest()[:10]
    modules: dict[str, Any] = {}
    for kind, ref in manifest.handler_refs.items():
        module, func = ref.split(":")
        try:
            if module not in modules:
                modules[module] = _load_module(path.parent, module, unique)
            fn = getattr(modules[module], func, None)
        except Exception as exc:  # noqa: BLE001 — any import failure makes the capability invalid
            problems.append(f"{kind} handler '{ref}' failed to import: {type(exc).__name__}: {exc}")
            continue
        if fn is None or not callable(fn):
            problems.append(f"{kind} handler '{ref}' is not a callable")
            continue
        if inspect.iscoroutinefunction(fn):
            problems.append(f"{kind} handler '{ref}' must be synchronous in manifest_version 1")
            continue
        bad = _check_arity(fn, HANDLER_ARITY[kind])
        if bad:
            problems.append(f"{kind} handler '{ref}' {bad}")
            continue
        manifest.handlers[kind] = fn
    if problems:
        raise ManifestError(src, problems)
    return manifest
