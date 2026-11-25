"""Content safety helpers shared by manifest validation, MCP metadata validation and result handling.

* instruction-override detection — integration-supplied text (descriptions, server metadata, tool output) is DATA.
  Descriptions reach the model's TRUSTED context section as tool descriptions, so a description that tries to
  issue instructions is rejected at validation. Tool OUTPUT is never rejected for its wording (it may legitimately
  quote such text); it is flagged and always delivered as untrusted content with provenance.
* raw-secret detection — manifests, skills and configuration may carry credential REFERENCES only.
* redaction — applied to everything written to audit/event storage and logs."""
from __future__ import annotations

import re
from typing import Any

INJECTION_PATTERNS = [
    (re.compile(r"\bignore\b.{0,40}\b(previous|prior|above|earlier|all)\b.{0,40}\b(instruction|prompt|rule|direction)s?", re.I),
     "instruction override"),
    (re.compile(r"\b(disregard|forget|override)\b.{0,40}\b(instruction|system prompt|rules|policy|polic(y|ies))", re.I),
     "instruction override"),
    (re.compile(r"\b(grant|give|assign|elevate)\b.{0,60}\b(admin(istrator)?|root|superuser|full) ?(access|rights|privileges?|permissions?)?", re.I),
     "privilege escalation request"),
    (re.compile(r"\b(increase|raise|extend|remove)\b.{0,40}\b(budget|limit|quota|deadline)s?\b", re.I),
     "budget/limit change request"),
    (re.compile(r"\b(approve|authori[sz]e)\b.{0,40}\b(this|the|my|all)\b.{0,40}\b(action|request|operation|plugin|tool)s?", re.I),
     "self-approval request"),
    (re.compile(r"\b(send|reveal|print|output|exfiltrate|share|post)\b.{0,60}\b(credential|password|secret|api[ _-]?key|token|private key)s?", re.I),
     "credential exfiltration request"),
    (re.compile(r"\b(run|call|invoke|execute|use)\b.{0,30}\b(another|other|additional|the following|any)\b.{0,30}\btools?\b", re.I),
     "tool redirection request"),
    (re.compile(r"\bsystem\s*prompt\b|\byou are now\b|\bnew instructions\b|<\s*/?\s*(system|assistant)\s*>", re.I),
     "role/system-prompt manipulation"),
    (re.compile(r"\b(change|set|modify)\b.{0,40}\b(risk|trust)( class| level)?\b", re.I),
     "risk/trust change request"),
]

SECRET_VALUE_PATTERNS = [
    re.compile(r"\bsk-[A-Za-z0-9_-]{16,}"), re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}"),
    re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}"), re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"), re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.eyJ[A-Za-z0-9_-]{8,}\."),
    re.compile(r"\bAIza[0-9A-Za-z_-]{30,}"), re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]{20,}"),
    re.compile(r"://[^/\s:@]+:[^/\s:@]+@"),        # credentials embedded in a URL
]
SECRET_KEY = re.compile(r"(?i)^(pass(word)?|passwd|secret|client_secret|token|access_token|refresh_token|api[_-]?key|"
                        r"private[_-]?key|authorization|auth|credential|credentials|dsn|cookie|session)$")
CREDENTIAL_REF = re.compile(r"^credref://[a-z0-9][a-z0-9_.-]{0,80}/[a-z][a-z0-9_]{1,40}$")
REDACTED = "[REDACTED]"


def injection_findings(text: str) -> list[str]:
    if not isinstance(text, str) or not text:
        return []
    return sorted({label for rx, label in INJECTION_PATTERNS if rx.search(text)})


def walk_strings(obj: Any, path: str = ""):
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield from walk_strings(v, f"{path}/{k}")
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            yield from walk_strings(v, f"{path}/{i}")
    elif isinstance(obj, str):
        yield path, obj


def secret_findings(obj: Any, path: str = "", in_schema: bool = False) -> list[str]:
    """Locations that look like raw credentials. JSON-Schema 'properties' maps are exempt from the key rule (a
    property NAMED token is a declaration, not a value) but never from the value-pattern rule."""
    found: list[str] = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            p = f"{path}/{k}"
            if not in_schema and SECRET_KEY.match(str(k)) and isinstance(v, str) and v.strip() and \
                    not CREDENTIAL_REF.match(v.strip()):
                found.append(f"{p}: value under a credential-like key (use a credref:// reference)")
            found += secret_findings(v, p, in_schema or k in ("properties", "input_schema", "output_schema",
                                                               "error_schema", "configuration_schema", "schemas",
                                                               "parameters", "$defs"))
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            found += secret_findings(v, f"{path}/{i}", in_schema)
    elif isinstance(obj, str):
        if any(rx.search(obj) for rx in SECRET_VALUE_PATTERNS):
            found.append(f"{path or '<value>'}: looks like a raw credential")
    return found


def redact(obj: Any, _depth: int = 0) -> Any:
    """Recursively redact credential-like keys and secret-looking values. Never raises."""
    if _depth > 40:
        return REDACTED
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            if SECRET_KEY.match(str(k)) and not (isinstance(v, str) and CREDENTIAL_REF.match(v)) and \
                    not isinstance(v, (dict, list, bool)) and v is not None:
                out[k] = REDACTED
            else:
                out[k] = redact(v, _depth + 1)
        return out
    if isinstance(obj, (list, tuple)):
        return [redact(v, _depth + 1) for v in obj]
    if isinstance(obj, str):
        s = obj
        for rx in SECRET_VALUE_PATTERNS:
            s = rx.sub(REDACTED, s)
        return s
    return obj


def remote_refs(schema: Any) -> list[str]:
    """JSON-Schema $ref values that are not local fragments (remote refs could fetch or change validation)."""
    out = []
    if isinstance(schema, dict):
        for k, v in schema.items():
            if k in ("$ref", "$dynamicRef", "$schema", "$id") and isinstance(v, str) and k != "$schema" and \
                    not v.startswith("#"):
                out.append(v)
            out += remote_refs(v)
    elif isinstance(schema, list):
        for v in schema:
            out += remote_refs(v)
    return out
