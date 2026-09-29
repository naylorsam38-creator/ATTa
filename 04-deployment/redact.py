"""v114: strip secrets from text before it leaves this server (the LLM repair step sends logs, build
records and file contents to an external API). Keys stay visible so the model can still reason about
"DB_PASSWORD is set"; values become MARK. Anything a repair tool is asked to WRITE that still contains
MARK is refused, so a redacted value can never be written back over the real one."""
import re

MARK = "[redacted-by-atta]"
# Names that hold secrets. Deliberately not bare PASS / AUTH / SESSION: "PASS: stage 6" and "author:"
# are results and metadata the repair step needs to read.
_NAME = (r"(?:[A-Za-z0-9_.-]*(?:PASSWORD|PASSWD|SECRET|TOKEN|API_?KEY|PRIVATE_?KEY|ACCESS_?KEY|CREDENTIAL|"
         r"AUTHORIZATION|AUTH_?(?:KEY|TOKEN|SECRET)|SESSION_?(?:KEY|SECRET)|SALT|DSN)[A-Za-z0-9_.-]*"
         r"|[A-Za-z0-9.-]+_PASS(?:_[A-Za-z0-9_.-]*)?)")
_PATTERNS = [
    # PEM private keys, whole block
    (re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.S), MARK),
    # Authorization header of any scheme: the whole value
    (re.compile(r"(?i)(\b(?:Proxy-)?Authorization\s*:\s*)(?=\S)(?!\S*\s*\[redacted)(?!(?:Bearer|Basic)\s)[^\n]+"), lambda m: m.group(1) + MARK),
    # Authorization headers
    (re.compile(r"(?i)(\b(?:Bearer|Basic)\s+)[A-Za-z0-9._~+/=-]{12,}"), lambda m: m.group(1) + MARK),
    # "name": "value"  (JSON / YAML with quotes)
    (re.compile(r'(?i)(["\']' + _NAME + r'["\']\s*:\s*)(["\'])(?!\2)(.+?)\2'), lambda m: m.group(1) + m.group(2) + MARK + m.group(2)),
    # NAME="quoted value, spaces allowed"
    (re.compile(r"(?i)(\b" + _NAME + r"\s*[=:]\s*)([\"'])(?!\[redacted)([^\n]*?)\2"), lambda m: m.group(1) + m.group(2) + MARK + m.group(2)),
    # NAME=value / NAME: value  (env files, compose, logs); -e NAME=value on command lines
    (re.compile(r"(?i)(\b" + _NAME + r"\s*[=:]\s*)(?![\s\"']*\[redacted)(?!(?:Bearer|Basic)\s)([\"']?)([^\s\"',;}]+)"), lambda m: m.group(1) + m.group(2) + MARK),
    # credentials inside URLs: scheme://user:pass@host
    (re.compile(r"(\b[a-z][a-z0-9+.-]*://[^/\s:@]+:)([^@\s/]+)(@)"), lambda m: m.group(1) + MARK + m.group(3)),
    # well-known key shapes
    (re.compile(r"\bAKIA[0-9A-Z]{16}\b"), MARK),
    (re.compile(r"\bsk-(?:ant-)?[A-Za-z0-9_-]{20,}\b"), MARK),
    (re.compile(r"\bgh[pousr]_[A-Za-z0-9]{30,}\b"), MARK),
]


def redact(text):
    if not isinstance(text, str) or not text:
        return text
    for pat, rep in _PATTERNS:
        text = pat.sub(rep, text)
    return text


def contains_mark(obj):
    if isinstance(obj, str):
        return MARK in obj
    if isinstance(obj, dict):
        return any(contains_mark(v) for v in obj.values())
    if isinstance(obj, (list, tuple)):
        return any(contains_mark(v) for v in obj)
    return False
