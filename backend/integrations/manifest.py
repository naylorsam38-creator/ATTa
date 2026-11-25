"""Integration manifest loading and validation (Stage 12 §5, §8). Reads and validates METADATA ONLY.

Nothing here imports, executes or probes integration code: handler modules are located (not imported) with the
same symlink/containment rules as the Stage 01A capability manifest loader. A manifest is rejected — never
partially accepted — when any problem is found. Unknown fields are rejected by the schema
(additionalProperties: false everywhere), so an unrecognised field can never silently grant authority."""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError

from backend.capability_registry.manifest import _module_file
from backend.capability_registry.package import PackageError, package_files
from backend.contracts import canonical_hash

from .content import injection_findings, remote_refs, secret_findings
from .types import (CUSTOM_PROVIDER_CAPABILITIES, OPERATION_MIN_EFFECT, RISK_FLOOR, RISK_ORDER, TYPE_CAPABILITY,
                    ConnectorOperation, ExecutionMode, IntegrationType, SideEffect)

MANIFEST_FILENAME = "integration.yaml"
INTEGRATION_API = "1"
MAX_SCHEMA_BYTES = 64 * 1024
RESERVED_FILES = ("capability.yaml",)
RESERVED_PREFIX = "_atta_"
CODE_SUFFIXES = (".py", ".pyw", ".so", ".pyd", ".dll", ".dylib", ".js", ".mjs", ".sh", ".bat", ".ps1", ".exe")
SHELLS = {"sh", "bash", "zsh", "dash", "ksh", "fish", "cmd", "cmd.exe", "powershell", "powershell.exe", "pwsh",
          "env", "sudo", "su", "doas", "xargs", "nohup", "setsid"}
_META = Draft202012Validator(json.loads(Path(__file__).with_name("integration.schema.json").read_text()))
TOOL_REF = re.compile(r"^((0[0-3][0-9]|040)\.[0-9]{3}|[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)+:[a-z][a-z0-9_]{0,40})$")


class IntegrationManifestError(Exception):
    def __init__(self, source: str, problems: list[str]):
        super().__init__(f"{source}: " + "; ".join(problems[:10]))
        self.source, self.problems = source, problems


@dataclass(frozen=True)
class IntegrationManifest:
    raw: dict
    source: Path
    manifest_digest: str          # sha256 over the canonical JSON of the manifest
    package_digest: str | None    # Stage 01A package digest of the source directory (None for config-only MCP)
    files: tuple[str, ...] = field(default_factory=tuple)

    @property
    def integration_id(self) -> str:
        return self.raw["integration_id"]

    @property
    def version(self) -> str:
        return self.raw["version"]

    @property
    def type(self) -> IntegrationType:
        return IntegrationType(self.raw["integration_type"])

    @property
    def directory(self) -> Path:
        return self.source.parent

    @property
    def tools(self) -> list[dict]:
        return list(self.raw.get("tools") or [])

    @property
    def permissions(self) -> frozenset[str]:
        return frozenset(self.raw.get("permissions_requested") or [])

    @property
    def max_risk(self) -> str:
        risks = [t["risk"] for t in self.tools] or ["low"]
        return max(risks, key=lambda r: RISK_ORDER[r])

    @property
    def network_hosts(self) -> tuple[str, ...]:
        return tuple(self.raw["network_requirements"].get("hosts") or [])

    @property
    def credential_names(self) -> tuple[str, ...]:
        return tuple(c["name"] for c in self.raw.get("credential_requirements") or [])

    @property
    def requirement(self) -> str:
        if self.type is IntegrationType.CUSTOM_PROVIDER:
            return self.raw.get("requirement") or "CAP-40"
        return TYPE_CAPABILITY[self.type]


def _schema_problems(where: str, schema: Any, must_be_object: bool) -> list[str]:
    out = []
    if not isinstance(schema, dict):
        return [f"{where} must be a JSON Schema object"]
    try:
        Draft202012Validator.check_schema(schema)
    except SchemaError as exc:
        out.append(f"{where} is not a valid JSON Schema: {exc.message}")
    if must_be_object and schema.get("type") != "object":
        out.append(f"{where} must have type 'object'")
    refs = remote_refs(schema)
    if refs:
        out.append(f"{where} uses non-local references {refs[:3]} (only '#/...' references are allowed)")
    if len(json.dumps(schema)) > MAX_SCHEMA_BYTES:
        out.append(f"{where} exceeds {MAX_SCHEMA_BYTES} bytes")
    return out


def validate_mcp_command(cmd: list[str], package_dir: Path | None, allowed_executables: frozenset[str]) -> list[str]:
    """argv only, no shells, no inline code; the executable must be the ATTa interpreter placeholder, a file inside
    the package, or an operator-allowlisted absolute path."""
    problems = []
    if not cmd:
        return ["mcp.command is required for the stdio transport"]
    exe = cmd[0]
    base = Path(exe).name.lower()
    if base in SHELLS:
        problems.append(f"mcp.command executable '{exe}' is a shell or launcher; argv execution only")
    if any(ch in a for a in cmd for ch in ("\n", "\r", "\x00")):
        problems.append("mcp.command contains control characters")
    if exe == "{python}":
        if any(a in ("-c", "-m") for a in cmd[1:2]):
            problems.append("mcp.command may not run inline code (-c) or arbitrary modules (-m); name a script file")
        script = cmd[1] if len(cmd) > 1 else None
        if script is None:
            problems.append("mcp.command '{python}' needs a script inside the package")
        elif package_dir is not None:
            problems += _inside(package_dir, script, "mcp.command script")
    elif exe.startswith(("/", "\\")) or re.match(r"^[A-Za-z]:", exe):
        if exe not in allowed_executables:
            problems.append(f"mcp.command executable '{exe}' is not in the operator allowlist "
                            "(integrations.allowed_mcp_executables)")
    elif package_dir is not None:
        problems += _inside(package_dir, exe, "mcp.command executable")
    else:
        problems.append(f"mcp.command executable '{exe}' must be inside the package or operator-allowlisted")
    return problems


def _inside(root: Path, rel: str, what: str) -> list[str]:
    if rel.startswith(("/", "\\", "~")) or ".." in rel.replace("\\", "/").split("/") or ":" in rel:
        return [f"{what} '{rel}' must be a package-relative path without '..'"]
    p = root / rel
    if p.is_symlink():
        return [f"{what} '{rel}' is a symlink"]
    if not p.is_file():
        return [f"{what} '{rel}' not found inside the package"]
    if root.resolve() not in p.resolve().parents:
        return [f"{what} '{rel}' resolves outside the package"]
    return []


def semantic_problems(data: dict, package_dir: Path | None, *, known_permissions: frozenset[str] | None = None,
                      allowed_executables: frozenset[str] = frozenset()) -> list[str]:
    p: list[str] = []
    itype = IntegrationType(data["integration_type"])
    mode = ExecutionMode(data["execution_mode"])
    tools = data.get("tools") or []
    if data["compatibility"]["integration_api"] != INTEGRATION_API:
        p.append(f"incompatible integration_api {data['compatibility']['integration_api']} (this ATTa supports "
                 f"{INTEGRATION_API})")
    # type <-> section <-> execution mode consistency
    expect = {IntegrationType.MCP_SERVER: "mcp", IntegrationType.SKILL: "skill", IntegrationType.PLUGIN: "plugin"}
    for t, sec in expect.items():
        if itype is t and sec not in data:
            p.append(f"{t.value} integrations require a '{sec}' section")
        if itype is not t and sec in data:
            p.append(f"'{sec}' section is only valid for {t.value} integrations")
    if itype is IntegrationType.MCP_SERVER:
        tr = data.get("mcp", {}).get("transport")
        want = {"stdio": ExecutionMode.MCP_STDIO, "streamable_http": ExecutionMode.MCP_HTTP}.get(tr)
        if mode is not want:
            p.append(f"MCP transport '{tr}' requires execution_mode {want.value if want else '?'}")
        if tr == "stdio":
            p += validate_mcp_command(data["mcp"].get("command") or [], package_dir, allowed_executables)
            if "url" in data["mcp"]:
                p.append("mcp.url is not used by the stdio transport")
        elif tr == "streamable_http":
            from urllib.parse import urlparse
            u = urlparse(data["mcp"].get("url") or "")
            if u.scheme not in ("http", "https") or not u.hostname:
                p.append("mcp.url must be an http(s) URL")
            elif u.hostname not in data["network_requirements"]["hosts"]:
                p.append(f"mcp.url host '{u.hostname}' is not declared in network_requirements.hosts")
            if u.username or u.password:
                p.append("mcp.url may not embed credentials")
            if "command" in data["mcp"]:
                p.append("mcp.command is not used by the HTTP transport")
    elif itype is IntegrationType.SKILL:
        if mode is not ExecutionMode.DECLARATIVE:
            p.append("SKILL integrations are DECLARATIVE")
        if tools:
            p.append("skills do not declare executable tools; they reference catalogue tools in skill.tool_references")
    else:
        if mode in (ExecutionMode.MCP_STDIO, ExecutionMode.MCP_HTTP, ExecutionMode.DECLARATIVE):
            p.append(f"execution_mode {mode.value} is not valid for {itype.value}")
    if itype is IntegrationType.CUSTOM_PROVIDER and data.get("requirement") not in (None, *CUSTOM_PROVIDER_CAPABILITIES):
        p.append(f"CUSTOM_PROVIDER requirement must be one of {sorted(CUSTOM_PROVIDER_CAPABILITIES)}")
    if itype is not IntegrationType.CUSTOM_PROVIDER and "requirement" in data:
        p.append("'requirement' is derived from integration_type and may only be set by CUSTOM_PROVIDER")
    if itype is not IntegrationType.SKILL and not tools and itype is not IntegrationType.PLUGIN:
        p.append(f"{itype.value} integrations must declare at least one tool")
    # descriptions reach the model's trusted context as tool descriptions: they may not issue instructions
    for where, text in [("description", data.get("description")), ("name", data.get("name"))] + \
            [(f"tools/{t.get('name')}/description", t.get("description")) for t in tools] + \
            [(f"capabilities/{c.get('id')}/description", c.get("description")) for c in data.get("capabilities") or []]:
        f = injection_findings(text or "")
        if f:
            p.append(f"{where} contains instruction-like text ({', '.join(f)}); descriptions are data")
    # permissions
    requested = set(data.get("permissions_requested") or [])
    if known_permissions is not None:
        unknown = sorted(requested - known_permissions)
        if unknown:
            p.append(f"permissions_requested not in the operator permission catalog: {unknown}")
    if any("*" in x for x in requested):
        p.append("wildcard permissions are never allowed")
    caps = {c["id"] for c in data.get("capabilities") or []}
    creds = {c["name"] for c in data.get("credential_requirements") or []}
    names: set[str] = set()
    mcp_names: set[str] = set()
    for t in tools:
        n = t["name"]
        w = f"tools/{n}"
        if n in names:
            p.append(f"duplicate tool name '{n}'")
        names.add(n)
        extra = sorted(set(t["permissions"]) - requested)
        if extra:
            p.append(f"{w} requires permissions not in permissions_requested: {extra}")
        eff = SideEffect(t["side_effect"])
        if RISK_ORDER[t["risk"]] < RISK_ORDER[RISK_FLOOR[eff]]:
            p.append(f"{w} declares risk '{t['risk']}' below the minimum '{RISK_FLOOR[eff]}' for {eff.value}")
        op = t.get("connector_operation")
        if itype is IntegrationType.CONNECTOR:
            if op is None:
                p.append(f"{w}: connector tools must declare connector_operation")
            else:
                need = OPERATION_MIN_EFFECT[ConnectorOperation(op)]
                order = list(SideEffect)
                if eff is SideEffect.READ_ONLY and need is not SideEffect.READ_ONLY:
                    p.append(f"{w}: operation {op} cannot be classified READ_ONLY (minimum {need.value})")
                elif need is not SideEffect.READ_ONLY and RISK_ORDER[RISK_FLOOR[eff]] < RISK_ORDER[RISK_FLOOR[need]]:
                    p.append(f"{w}: side_effect {eff.value} is weaker than {need.value} required for {op}")
                if op.lower() not in t["operations"]:
                    p.append(f"{w}: operations must include '{op.lower()}' for connector_operation {op}")
                _ = order
        if t.get("capability") and t["capability"] not in caps:
            p.append(f"{w}: capability '{t['capability']}' is not declared in capabilities")
        for c in t.get("credentials") or []:
            if c not in creds:
                p.append(f"{w}: credential '{c}' is not declared in credential_requirements")
        for key, obj in (("input_schema", True), ("output_schema", True), ("error_schema", True)):
            p += _schema_problems(f"{w}/{key}", t[key], obj)
        if itype is IntegrationType.MCP_SERVER:
            if "handler" in t:
                p.append(f"{w}: MCP tools are executed by the ATTa MCP bridge; 'handler' is not allowed")
            mn = t.get("mcp_name", n)
            if mn in mcp_names:
                p.append(f"duplicate mcp_name '{mn}'")
            mcp_names.add(mn)
        else:
            if "mcp_name" in t:
                p.append(f"{w}: mcp_name is only valid for MCP_SERVER integrations")
            if "handler" not in t:
                p.append(f"{w}: a handler is required")
            elif package_dir is not None:
                mod = t["handler"].split(":")[0]
                if mod.split(".")[0].startswith(RESERVED_PREFIX):
                    p.append(f"{w}: handler module names starting with '{RESERVED_PREFIX}' are reserved")
                try:
                    if _module_file(package_dir, mod) is None:
                        p.append(f"{w}: handler module '{mod}' not found inside the package")
                except ValueError as exc:
                    p.append(f"{w}: {exc}")
        rs = t.get("resource_scope") or {}
        for a in (rs.get("path_arguments") or []) + (rs.get("url_arguments") or []):
            if a not in (t["input_schema"].get("properties") or {}):
                p.append(f"{w}: resource_scope argument '{a}' is not an input property")
        if rs.get("url_arguments") and not data["network_requirements"]["hosts"]:
            p.append(f"{w}: url arguments require declared network_requirements.hosts")
    h = (data.get("health") or {}).get("handler")
    if h and package_dir is not None:
        try:
            if _module_file(package_dir, h.split(":")[0]) is None:
                p.append(f"health handler module for '{h}' not found inside the package")
        except ValueError as exc:
            p.append(f"health handler: {exc}")
    p += _schema_problems("configuration_schema", data["configuration_schema"], True)
    # skills: files inside the package and well-formed tool references
    sk = data.get("skill")
    if sk:
        if package_dir is not None:
            p += _inside(package_dir, sk["instructions"], "skill.instructions")
            for t in sk.get("templates") or []:
                p += _inside(package_dir, t, "skill template")
            for name, sch in (sk.get("schemas") or {}).items():
                p += _schema_problems(f"skill.schemas/{name}", sch, False)
        refs = set(sk["tool_references"])
        for r in refs:
            if not TOOL_REF.match(r):
                p.append(f"skill tool reference '{r}' must be a tool number CCC.TTT or '<id>:<tool>'")
        for wf in sk.get("workflows") or []:
            p += _workflow_problems(wf, refs)
    pl = data.get("plugin")
    if pl:
        for wf in pl["contributes"].get("workflows") or []:
            p += _workflow_problems(wf, None)
        for name, sch in (pl["contributes"].get("schemas") or {}).items():
            p += _schema_problems(f"plugin.schemas/{name}", sch, False)
    # raw credentials anywhere in the manifest
    p += [f"raw credential material: {s}" for s in secret_findings(data)]
    return p


def _workflow_problems(wf: dict, refs: set[str] | None) -> list[str]:
    p = _schema_problems(f"workflow {wf['id']} parameters", wf["parameters"], True)
    for i, s in enumerate(wf["steps"]):
        if not TOOL_REF.match(s["tool"]):
            p.append(f"workflow {wf['id']} step {i}: tool '{s['tool']}' is not a tool reference")
        elif refs is not None and s["tool"] not in refs:
            p.append(f"workflow {wf['id']} step {i}: tool '{s['tool']}' is not in skill.tool_references")
    return p


def integrity_problems(data: dict, package_dir: Path) -> tuple[list[str], tuple[str, ...], str | None]:
    """Every package file must be listed with its SHA-256 (except integration.yaml itself); code packages must carry
    integrity metadata. Returns (problems, files, package_digest)."""
    from backend.capability_registry.package import package_digest
    try:
        entries = package_files(package_dir)
    except PackageError as exc:
        return [f"package structure: {exc}"], (), None
    files = tuple(rel for rel, _ in entries)
    problems = []
    for rel in files:
        name = rel.rsplit("/", 1)[-1]
        if rel in RESERVED_FILES or name.startswith(RESERVED_PREFIX):
            problems.append(f"package file '{rel}' uses a name reserved for ATTa-generated files")
    listed = (data.get("integrity") or {}).get("files")
    payload = [f for f in files if f != MANIFEST_FILENAME]
    has_code = any(f.lower().endswith(CODE_SUFFIXES) for f in payload)
    if listed is None:
        if has_code:
            problems.append("packages containing code must declare integrity.files (sha256 of every file)")
    else:
        missing = sorted(set(payload) - set(listed))
        extra = sorted(set(listed) - set(payload))
        if missing:
            problems.append(f"files not covered by integrity metadata: {missing[:10]}")
        if extra:
            problems.append(f"integrity metadata lists files that do not exist: {extra[:10]}")
        by_rel = dict(entries)
        for rel, digest in listed.items():
            if rel in by_rel and hashlib.sha256(by_rel[rel].read_bytes()).hexdigest() != digest:
                problems.append(f"integrity mismatch for '{rel}'")
    try:
        pd = package_digest(package_dir)
    except PackageError as exc:
        return problems + [str(exc)], files, None
    return problems, files, pd


def load_integration_manifest(path: Path, *, known_permissions: frozenset[str] | None = None,
                              allowed_executables: frozenset[str] = frozenset()) -> IntegrationManifest:
    src = str(path)
    try:
        raw = path.read_bytes()
        data = yaml.safe_load(raw)
    except (OSError, yaml.YAMLError) as exc:
        raise IntegrationManifestError(src, [f"unreadable manifest: {exc}"]) from exc
    return validate_manifest_data(data, path, known_permissions=known_permissions,
                                  allowed_executables=allowed_executables)


def validate_manifest_data(data: Any, path: Path, *, known_permissions: frozenset[str] | None = None,
                           allowed_executables: frozenset[str] = frozenset(), package: bool = True
                           ) -> IntegrationManifest:
    src = str(path)
    if not isinstance(data, dict):
        raise IntegrationManifestError(src, ["manifest must be a mapping"])
    problems = []
    for e in sorted(_META.iter_errors(data), key=lambda e: list(e.absolute_path)):
        loc = "/".join(str(x) for x in e.absolute_path) or "<root>"
        msg = e.message
        if e.validator == "additionalProperties":
            msg += " — unknown fields are rejected so they can never alter security behaviour"
        problems.append(f"{loc}: {msg}")
    if problems:
        raise IntegrationManifestError(src, problems)
    pkg_dir = path.parent if package else None
    problems = semantic_problems(data, pkg_dir, known_permissions=known_permissions,
                                 allowed_executables=allowed_executables)
    files: tuple[str, ...] = ()
    pdigest = None
    if pkg_dir is not None:
        ip, files, pdigest = integrity_problems(data, pkg_dir)
        problems += ip
    if problems:
        raise IntegrationManifestError(src, problems)
    return IntegrationManifest(raw=data, source=path, manifest_digest=canonical_hash(data), package_digest=pdigest,
                               files=files)


def write_integrity(package_dir: Path) -> dict:
    """Developer/test helper: (re)compute integrity.files for a package directory and write it into the manifest."""
    mpath = package_dir / MANIFEST_FILENAME
    data = yaml.safe_load(mpath.read_text())
    files = {rel: hashlib.sha256(p.read_bytes()).hexdigest() for rel, p in package_files(package_dir)
             if rel != MANIFEST_FILENAME}
    data["integrity"] = {"files": files}
    mpath.write_text(yaml.safe_dump(data, sort_keys=False))
    return data
