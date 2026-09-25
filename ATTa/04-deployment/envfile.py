#!/usr/bin/env python3
"""Read <ROOT>/.env as data, never as shell (v114.1).

bootstrap.sh used to `. .env`, which runs the file as a shell script: a line like
X=$(anything) executed as root. This reads it line by line instead.

Accepted:  blank lines, # comments, and NAME=value where NAME is [A-Z_][A-Z0-9_]* and value is
           plain text, optionally wrapped in one pair of matching quotes.
Refused (the whole file, with the line number): anything else, and any value containing $ ` \\
           or a control character. No expansion or substitution of any kind happens.
Unknown names: reported, never exported. The services still receive the full file through
           systemd's EnvironmentFile=, which doesn't run shell either.

    python3 envfile.py check FILE
    python3 envfile.py run FILE --keys A,B,C -- command args...   (only those keys are set)
"""
from __future__ import annotations
import os, re, sys

NAME_RE = re.compile(r"^[A-Z_][A-Z0-9_]*$")
UNSAFE_RE = re.compile(r"[$`\\\x00-\x08\x0b-\x1f\x7f]")
# Names this system reads. Anything else in .env is reported so a typo or a planted line is seen.
KNOWN_PREFIXES = ("APP_BUILDER_", "ATTA_", "COOLIFY_", "ANTHROPIC_")
KNOWN_NAMES = {"ALERT_WEBHOOK_URL", "HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY"}


class EnvFileError(ValueError):
    pass


def parse(text: str) -> tuple[dict, list[str]]:
    """(values, warnings). Raises EnvFileError on the first bad line; values are never echoed."""
    values, warnings = {}, []
    for n, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            raise EnvFileError(f"line {n}: not NAME=value")
        name, value = line.split("=", 1)
        if not NAME_RE.match(name):
            raise EnvFileError(f"line {n}: {name[:40]!r} is not a plain variable name")
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        if any(q in value for q in "\"'"):
            raise EnvFileError(f"line {n} ({name}): stray quote in the value")
        if UNSAFE_RE.search(value):
            raise EnvFileError(f"line {n} ({name}): value contains $, `, \\ or a control character; "
                               "plain text only (nothing is expanded)")
        if not (name in KNOWN_NAMES or name.startswith(KNOWN_PREFIXES)):
            warnings.append(f"line {n}: {name} is not a setting this system reads; ignored here")
            continue
        values[name] = value
    return values, warnings


def load(path: str) -> tuple[dict, list[str]]:
    with open(path, encoding="utf-8") as f:
        return parse(f.read())


def main(argv: list[str]) -> int:
    if len(argv) >= 2 and argv[0] == "check":
        try:
            _, warnings = load(argv[1])
        except (OSError, UnicodeDecodeError, EnvFileError) as e:
            print(f"envfile: {argv[1]}: {e}", file=sys.stderr); return 1
        for w in warnings:
            print(f"envfile: {argv[1]}: {w}", file=sys.stderr)
        return 0
    if len(argv) >= 5 and argv[0] == "run" and argv[2] == "--keys" and "--" in argv:
        sep = argv.index("--")
        keys = [k for k in argv[3].split(",") if k]
        cmd = argv[sep + 1:]
        if not cmd:
            print("envfile: nothing to run", file=sys.stderr); return 2
        try:
            values, warnings = load(argv[1])
        except (OSError, UnicodeDecodeError, EnvFileError) as e:
            print(f"envfile: {argv[1]}: {e}", file=sys.stderr); return 1
        for w in warnings:
            print(f"envfile: {argv[1]}: {w}", file=sys.stderr)
        env = dict(os.environ)
        env.update({k: values[k] for k in keys if k in values})
        os.execvpe(cmd[0], cmd, env)
    print(__doc__, file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
