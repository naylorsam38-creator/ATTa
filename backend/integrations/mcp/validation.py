"""Validation of MCP server metadata BEFORE anything is registered (Stage 12 §15).

Server metadata (names, descriptions, schemas, annotations, instructions) is untrusted. A server cannot add tools
after approval, change a declared schema, under-declare side effects through annotations, or smuggle instructions
through descriptions. Findings are returned; the caller decides (install verification fails / health degrades)."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError

from backend.contracts import canonical_hash

from ..content import injection_findings, remote_refs
from ..types import SideEffect

NAME = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")
MAX_DESCRIPTION = 2000
MAX_SCHEMA_BYTES = 64 * 1024


@dataclass
class ToolCheck:
    name: str
    declared: bool
    accepted: bool
    schema_hash: str
    problems: list[str] = field(default_factory=list)
    metadata: dict = field(default_factory=dict)


@dataclass
class ServerCheck:
    tools: list[ToolCheck]
    malicious: list[str]          # metadata trying to issue instructions -> server BLOCKED
    missing: list[str]            # declared tools the server does not offer -> install fails
    undeclared: list[str]         # offered but never approved -> ignored (never registered)
    resources: list[dict]
    prompts: list[dict]

    @property
    def ok(self) -> bool:
        return not self.malicious and not self.missing and all(t.accepted for t in self.tools if t.declared)

    def summary(self) -> dict:
        return {"ok": self.ok, "malicious": self.malicious, "missing": self.missing, "undeclared": self.undeclared,
                "rejected": {t.name: t.problems for t in self.tools if t.declared and not t.accepted}}


def _schema_problems(schema) -> list[str]:
    if not isinstance(schema, dict):
        return ["inputSchema must be an object"]
    out = []
    try:
        Draft202012Validator.check_schema(schema)
    except SchemaError as exc:
        out.append(f"inputSchema invalid: {exc.message}")
    if schema.get("type") != "object":
        out.append("inputSchema must have type 'object'")
    if remote_refs(schema):
        out.append("inputSchema uses non-local $ref")
    if len(json.dumps(schema)) > MAX_SCHEMA_BYTES:
        out.append("inputSchema too large")
    return out


def check_server(declared_tools: list[dict], server_tools: list, resources: list | None = None,
                 prompts: list | None = None) -> ServerCheck:
    by_mcp = {t.get("mcp_name", t["name"]): t for t in declared_tools}
    checks: list[ToolCheck] = []
    malicious: list[str] = []
    seen: set[str] = set()
    for item in server_tools:
        if not isinstance(item, dict) or not isinstance(item.get("name"), str):
            malicious.append("server listed a malformed tool entry")
            continue
        name = item["name"]
        probs = []
        if not NAME.match(name):
            probs.append("tool name has invalid characters or length")
        if name in seen:
            probs.append("duplicate tool name in server listing (collision)")
        seen.add(name)
        texts = [item.get("description"), item.get("title")]
        ann = item.get("annotations") if isinstance(item.get("annotations"), dict) else {}
        texts.append(ann.get("title"))
        for t in texts:
            if t is not None and not isinstance(t, str):
                probs.append("non-string description/title")
            elif isinstance(t, str):
                if len(t) > MAX_DESCRIPTION:
                    probs.append("description too long")
                f = injection_findings(t)
                if f:
                    malicious.append(f"tool '{name[:64]}' metadata contains instruction-like text: {', '.join(f)}")
        schema = item.get("inputSchema")
        probs += _schema_problems(schema)
        sh = canonical_hash(schema) if isinstance(schema, dict) else ""
        d = by_mcp.get(name)
        if d is not None:
            if isinstance(schema, dict) and canonical_hash(schema) != canonical_hash(d["input_schema"]):
                probs.append("server inputSchema differs from the approved manifest (revalidation required)")
            effect = SideEffect(d["side_effect"])
            if ann.get("destructiveHint") is True and effect in (SideEffect.READ_ONLY, SideEffect.REVERSIBLE_WRITE):
                probs.append("server marks the tool destructive but the manifest declares a weaker side effect")
            if ann.get("readOnlyHint") is False and ann.get("destructiveHint") is not False and \
                    effect is SideEffect.READ_ONLY and "readOnlyHint" in ann:
                probs.append("server says the tool is not read-only but the manifest declares READ_ONLY")
        meta = {"name": name, "description": (item.get("description") or "")[:500] if isinstance(
            item.get("description"), str) else None, "annotations": ann, "input_schema_hash": sh}
        checks.append(ToolCheck(name, d is not None, d is not None and not probs, sh, probs, meta))
    offered = {c.name for c in checks}
    missing = sorted(set(by_mcp) - offered)
    undeclared = sorted(offered - set(by_mcp))
    res_out, pr_out = [], []
    for r in resources or []:
        if isinstance(r, dict):
            f = injection_findings(str(r.get("description", ""))) + injection_findings(str(r.get("name", "")))
            if f:
                malicious.append(f"resource '{str(r.get('uri'))[:100]}' metadata contains instruction-like text")
            res_out.append({"uri": str(r.get("uri"))[:500], "name": str(r.get("name"))[:200],
                            "mimeType": r.get("mimeType") if isinstance(r.get("mimeType"), str) else None})
    for p in prompts or []:
        if isinstance(p, dict):
            if injection_findings(str(p.get("description", ""))):
                malicious.append(f"prompt '{str(p.get('name'))[:64]}' metadata contains instruction-like text")
            pr_out.append({"name": str(p.get("name"))[:200], "arguments": len(p.get("arguments") or [])})
    return ServerCheck(checks, sorted(set(malicious)), missing, undeclared, res_out, pr_out)
