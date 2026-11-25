#!/usr/bin/env python3
"""Scaffold a new capability: python scripts/new_capability.py area.name [--requirement CAP-NN] [--dir capabilities]

Creates <dir>/<area_name>/capability.yaml and handler.py with working (not stubbed) health and verify handlers.
The execute handler raises NotImplementedError until written, and the manifest is created DISABLED, so the
registry reports it as 'disabled' — it can never be executed before its author enables it."""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

MANIFEST = """manifest_version: 1
id: {id}
version: 0.1.0
description: {description}
{requirement}handlers:
  execute: handler:execute
  healthcheck: handler:healthcheck
  verify: handler:verify
input_schema: {{type: object, additionalProperties: false, properties: {{}}}}
output_schema: {{type: object, additionalProperties: false, required: [result], properties: {{result: {{type: string}}}}}}
permissions: [{perm}]
risk: {risk}
dependencies: {{capabilities: [], services: [], python: []}}
timeout_s: 30
enabled: false
"""

HANDLER = '''"""{id}"""


def execute(payload: dict, context) -> dict:
    raise NotImplementedError("{id}: write the execute handler, then set enabled: true")


def healthcheck() -> dict:
    return {{"healthy": True}}


def verify(payload: dict, output: dict) -> dict:
    ok = isinstance(output.get("result"), str)
    return {{"verified": ok, "problems": [] if ok else ["result missing"]}}
'''


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("id")
    ap.add_argument("--requirement")
    ap.add_argument("--risk", default="low", choices=["low", "medium", "high", "critical"])
    ap.add_argument("--description", default=None)
    ap.add_argument("--dir", default=str(ROOT / "capabilities"))
    a = ap.parse_args()
    if not re.fullmatch(r"[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)+", a.id):
        print("id must look like area.name (lowercase, dot separated)", file=sys.stderr)
        return 2
    target = Path(a.dir) / a.id.replace(".", "_")
    if target.exists():
        print(f"{target} already exists", file=sys.stderr)
        return 2
    target.mkdir(parents=True)
    (target / "capability.yaml").write_text(MANIFEST.format(
        id=a.id, description=a.description or f"{a.id} capability (describe what it does).",
        requirement=f"requirement: {a.requirement}\n" if a.requirement else "", perm=a.id + ".execute",
        risk=a.risk))
    (target / "handler.py").write_text(HANDLER.format(id=a.id))
    print(f"created {target} (disabled until you implement execute and set enabled: true)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
