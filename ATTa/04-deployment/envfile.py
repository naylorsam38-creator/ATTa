#!/usr/bin/env python3
"""Read <ROOT>/.env as data, never as shell (v114.1; strict rules v117).

Nothing in ATTa may `source`, `.`, `eval` or `set -a` an env file: a line like X=$(anything) would run as
root. Everything reads it through this file instead, and the services get it through systemd's
EnvironmentFile=, which doesn't run shell either. The rules below are the subset of the format that this
parser and systemd read IDENTICALLY, so the value a check sees is the value a service gets.

A line is one of:
    (blank)                     ignored
    # comment                   ignored (a whole line only; # inside a value is refused unless quoted)
    NAME=value                  NAME is [A-Z_][A-Z0-9_]*, no spaces around '='
    NAME="value"  NAME='value'  one pair of matching quotes around the whole value; kept literally inside

Refused, with the line number and never the value (the WHOLE file is refused, so nothing half-applies):
  - `export NAME=...`, `NAME =`, lowercase or dashed names, a line with no '='
  - $, `, \\ anywhere in a value, quoted or not (nothing is expanded or escaped, ever)
  - a control character anywhere in the file, including a carriage return (CRLF line endings) or a NUL
  - unquoted values holding anything but letters, digits and _ . / : , @ % + = - (quote them); so every file
    this accepts is also inert under `sh`: same values, nothing runs (tests/test_v117_envfile.py proves it)
  - a quoted value that contains its own kind of quote, or text after the closing quote
  - the same NAME twice (which one wins would be a guess; say it once)
  - names that change how programs load (LD_*, PYTHON*, PATH, BASH_ENV, NODE_OPTIONS, ...): systemd would hand
    them to every service
Unknown names: reported as a warning and never exported by `run`/`get`; systemd still gives them to services.

    python3 envfile.py check FILE                         exit 0 = file is valid (warnings on stderr)
    python3 envfile.py get FILE NAME [DEFAULT]            print one value (DEFAULT if unset or empty)
    python3 envfile.py run FILE --keys A,B,C -- cmd...    run cmd with only those keys added to its environment
    python3 envfile.py run FILE --all -- cmd...           ... with every known key added
"""
from __future__ import annotations
import os, re, sys

NAME_RE = re.compile(r"^[A-Z_][A-Z0-9_]*$")
# Never allowed anywhere in a value.
FORBIDDEN_RE = re.compile(r"[$`\\]")
# Unquoted values: only characters that mean nothing to a shell or to systemd. So a file this accepts is ALSO
# inert if someone ever does source it (same values, nothing runs): anything else has to be quoted.
UNQUOTED_RE = re.compile(r"^[A-Za-z0-9_./:,@%+=-]*$")
# Control characters anywhere in the file (tab is allowed only inside quotes, see below).
CONTROL_RE = re.compile(r"[\x00-\x08\x0a-\x1f\x7f]")
# Names this system reads. Anything else in .env is reported so a typo or a planted line is seen.
KNOWN_PREFIXES = ("APP_BUILDER_", "ATTA_", "COOLIFY_", "ANTHROPIC_", "DOCKERHUB_")
KNOWN_NAMES = {"ALERT_WEBHOOK_URL", "HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY"}
# Names that change how a program is loaded or where it looks for code. systemd would pass them to every
# service, so a planted line could run code as the service user: refused outright.
DANGEROUS_EXACT = {"PATH", "BASH_ENV", "ENV", "SHELLOPTS", "BASHOPTS", "IFS", "PS4", "PROMPT_COMMAND",
                   "NODE_OPTIONS", "NODE_PATH", "PERL5LIB", "PERL5OPT", "RUBYOPT", "RUBYLIB", "GCONV_PATH",
                   "HOSTALIASES", "LOCALDOMAIN", "RES_OPTIONS", "TMPDIR", "HOME", "SSL_CERT_FILE",
                   "SSL_CERT_DIR", "REQUESTS_CA_BUNDLE", "CURL_CA_BUNDLE", "OPENSSL_CONF", "GIT_SSH",
                   "GIT_SSH_COMMAND", "GIT_EXEC_PATH", "GIT_CONFIG_GLOBAL", "GIT_CONFIG_SYSTEM", "EDITOR", "PAGER"}
DANGEROUS_PREFIXES = ("LD_", "PYTHON", "DYLD_", "MALLOC_", "GLIBC_", "GCONV_", "BASH_FUNC_", "SYSTEMD_")


class EnvFileError(ValueError):
    pass


def _value(n: int, name: str, raw: str) -> str:
    if FORBIDDEN_RE.search(raw):
        raise EnvFileError(f"line {n} ({name}): value contains $, ` or \\; plain text only (nothing is expanded)")
    if raw[:1] in ("'", '"'):
        q = raw[0]
        end = raw.find(q, 1)
        if end < 0:
            raise EnvFileError(f"line {n} ({name}): opening {q} has no closing {q}")
        if end != len(raw) - 1:
            raise EnvFileError(f"line {n} ({name}): text after the closing quote, or a {q} inside a {q}-quoted value")
        return raw[1:-1]
    if any(c in raw for c in "\"'"):
        raise EnvFileError(f"line {n} ({name}): stray quote in the value; quote the whole value instead")
    if not UNQUOTED_RE.fullmatch(raw):
        raise EnvFileError(f"line {n} ({name}): unquoted value holds a space or one of ; & | < > ( ) # ~ * ? ! "
                           "[ ] { } ^; put the whole value in quotes")
    return raw


def is_dangerous(name: str) -> bool:
    return name in DANGEROUS_EXACT or name.startswith(DANGEROUS_PREFIXES)


def is_known(name: str) -> bool:
    return name in KNOWN_NAMES or name.startswith(KNOWN_PREFIXES)


def parse(text: str) -> tuple[dict, list[str]]:
    """(values of KNOWN names, warnings). Raises EnvFileError on the first bad line; values are never echoed."""
    if text.startswith("﻿"):
        raise EnvFileError("line 1: file starts with a byte-order mark; save it as plain UTF-8")
    values, warnings, seen = {}, [], {}
    # split("\n") and not splitlines(): splitlines() would silently accept \r, \v, \f, \x1c.. as line breaks.
    for n, raw in enumerate(text.split("\n"), 1):
        if CONTROL_RE.search(raw.replace("\t", "")) or "\r" in raw:
            raise EnvFileError(f"line {n}: control character (a carriage return means CRLF line endings; use LF)")
        line = raw.strip(" \t")
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            raise EnvFileError(f"line {n}: not NAME=value")
        name, value = line.split("=", 1)
        if not NAME_RE.fullmatch(name):
            if name.startswith("export "):
                raise EnvFileError(f"line {n}: 'export' is shell syntax; write NAME=value")
            raise EnvFileError(f"line {n}: {name[:40]!r} is not a plain variable name (A-Z, 0-9, _; no spaces)")
        if is_dangerous(name):
            raise EnvFileError(f"line {n}: {name} changes how programs load and may not be set in .env")
        if name in seen:
            raise EnvFileError(f"line {n}: {name} is already set on line {seen[name]}; set each name once")
        seen[name] = n
        v = _value(n, name, value)
        if not is_known(name):
            warnings.append(f"line {n}: {name} is not a setting this system reads; ignored here")
            continue
        values[name] = v
    return values, warnings


def load(path: str) -> tuple[dict, list[str]]:
    with open(path, encoding="utf-8") as f:
        return parse(f.read())


def _load_or_report(path: str):
    try:
        values, warnings = load(path)
    except (OSError, UnicodeDecodeError, EnvFileError) as e:
        print(f"envfile: {path}: {e}", file=sys.stderr)
        return None
    for w in warnings:
        print(f"envfile: {path}: {w}", file=sys.stderr)
    return values


def main(argv: list[str]) -> int:
    if len(argv) == 2 and argv[0] == "check":
        return 0 if _load_or_report(argv[1]) is not None else 1
    if len(argv) in (3, 4) and argv[0] == "get":
        if not NAME_RE.fullmatch(argv[2]):
            print(f"envfile: {argv[2][:40]!r} is not a variable name", file=sys.stderr); return 2
        try:
            values, _ = load(argv[1])
        except (OSError, UnicodeDecodeError, EnvFileError) as e:
            print(f"envfile: {argv[1]}: {e}", file=sys.stderr); return 1
        v = values.get(argv[2], "")
        print(v if v != "" else (argv[3] if len(argv) == 4 else ""))
        return 0
    if len(argv) >= 4 and argv[0] == "run" and "--" in argv:
        sep = argv.index("--")
        opts = argv[2:sep]
        if opts == ["--all"]:
            keys = None
        elif len(opts) == 2 and opts[0] == "--keys":
            keys = [k for k in opts[1].split(",") if k]
        else:
            print(__doc__, file=sys.stderr); return 2
        cmd = argv[sep + 1:]
        if not cmd:
            print("envfile: nothing to run", file=sys.stderr); return 2
        values = _load_or_report(argv[1])
        if values is None:
            return 1
        env = dict(os.environ)
        env.update(values if keys is None else {k: values[k] for k in keys if k in values})
        os.execvpe(cmd[0], cmd, env)
    print(__doc__, file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
