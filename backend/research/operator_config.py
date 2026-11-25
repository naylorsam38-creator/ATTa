"""Operator tool for the PROTECTED configuration Stage 11 needs (§4, §29) — controlled integration.

    python -m backend.research.operator_config check
    python -m backend.research.operator_config render --out <directory>

`check` verifies that the proposed Stage 11 tool numbers and permissions merge into the CURRENT
configuration/tool_numbers.yaml and configuration/permissions.yaml without conflicts (no number reused, no tool with
two numbers, no permission redefined differently, numbers match capability CAP-11/CAP-12).
`render` writes MERGED COPIES to another directory for the operator to review and copy into place, and prints their
SHA-256 for re-sealing. It never edits a protected file in place (exit code 2 if --out is the configuration
directory). Signing the packages stays a separate operator step (python -m backend.capability_registry trust ...)."""
from __future__ import annotations

import argparse
import hashlib
import sys
from pathlib import Path

import yaml

from backend.capability_registry.numbering import NumberingError, load_numbers
from backend.policy_engine.catalog import CatalogError, load_catalog
from backend.settings import REPO_ROOT

PROPOSED_NUMBERS = REPO_ROOT / "documentation" / "stage11" / "tool_numbers.stage11.yaml"
PROPOSED_PERMS = REPO_ROOT / "documentation" / "stage11" / "permissions.stage11.yaml"
CAPABILITY_OF = {"research.web": "011", "research.evidence": "012"}


def merged(numbers_file: Path, permissions_file: Path) -> tuple[dict, dict, list[str]]:
    problems: list[str] = []
    cur_numbers = yaml.safe_load(numbers_file.read_text(encoding="utf-8")) or {}
    cur_perms = yaml.safe_load(permissions_file.read_text(encoding="utf-8")) or {}
    prop_numbers = yaml.safe_load(PROPOSED_NUMBERS.read_text(encoding="utf-8"))["tools"]
    prop_perms = yaml.safe_load(PROPOSED_PERMS.read_text(encoding="utf-8"))
    tools = dict(cur_numbers.get("tools") or {})
    by_tool = {v["tool"]: k for k, v in tools.items()}
    for num, entry in prop_numbers.items():
        cap = entry["tool"].split(":")[0]
        if not num.startswith(CAPABILITY_OF.get(cap, "???") + "."):
            problems.append(f"{num} does not belong to the capability number of {cap}")
        if num in tools and tools[num]["tool"] != entry["tool"]:
            problems.append(f"number {num} is already assigned to {tools[num]['tool']}")
        elif entry["tool"] in by_tool and by_tool[entry["tool"]] != num:
            problems.append(f"{entry['tool']} already has number {by_tool[entry['tool']]}")
        elif num in tools and tools[num].get("retired"):
            problems.append(f"number {num} is retired and can never be reassigned")
        else:
            tools[num] = {"tool": entry["tool"]}
    perms = dict(cur_perms.get("permissions") or {})
    for name, desc in prop_perms["permissions"].items():
        if name in perms and perms[name] != desc:
            problems.append(f"permission {name} already exists with a different description")
        perms[name] = desc
    granted = list(dict.fromkeys(list(cur_perms.get("controller_granted_permissions") or []) +
                                 list(prop_perms["controller_granted_permissions"])))
    new_perms = dict(cur_perms) | {"permissions": perms, "controller_granted_permissions": granted}
    new_numbers = dict(cur_numbers) | {"tools": dict(sorted(tools.items()))}
    return new_numbers, new_perms, problems


def _validate(numbers: dict, perms: dict, tmp: Path) -> list[str]:
    tmp.mkdir(parents=True, exist_ok=True)
    (tmp / "tool_numbers.yaml").write_text(yaml.safe_dump(numbers, sort_keys=False))
    (tmp / "permissions.yaml").write_text(yaml.safe_dump(perms, sort_keys=False))
    out = []
    try:
        load_numbers(tmp / "tool_numbers.yaml")
    except NumberingError as exc:
        out.append(f"merged tool_numbers.yaml invalid: {exc}")
    try:
        load_catalog(tmp / "permissions.yaml")
    except CatalogError as exc:
        out.append(f"merged permissions.yaml invalid: {exc}")
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m backend.research.operator_config")
    ap.add_argument("cmd", choices=["check", "render"])
    ap.add_argument("--config-dir", default=str(REPO_ROOT / "configuration"))
    ap.add_argument("--out")
    a = ap.parse_args(argv)
    cfg = Path(a.config_dir).resolve()
    numbers, perms, problems = merged(cfg / "tool_numbers.yaml", cfg / "permissions.yaml")
    import tempfile
    with tempfile.TemporaryDirectory() as t:
        problems += _validate(numbers, perms, Path(t))
    if problems:
        print("CONFLICTS:\n  " + "\n  ".join(problems))
        return 1
    if a.cmd == "check":
        print(f"OK: {len(numbers['tools'])} tool numbers, {len(perms['permissions'])} permissions after merge; "
              "no conflicts")
        return 0
    if not a.out:
        print("render needs --out <directory>")
        return 2
    out = Path(a.out).resolve()
    if out == cfg or out == (REPO_ROOT / "configuration").resolve():
        print("refusing to write into the configuration directory: protected files are copied by the operator")
        return 2
    out.mkdir(parents=True, exist_ok=True)
    header = "# Merged by backend.research.operator_config (Stage 11). Review, copy into configuration/, re-seal.\n"
    for name, doc in (("tool_numbers.yaml", numbers), ("permissions.yaml", perms)):
        (out / name).write_text(header + yaml.safe_dump(doc, sort_keys=False))
        print(f"{name}: sha256 {hashlib.sha256((out / name).read_bytes()).hexdigest()}")
    print("next: copy both files into configuration/, sign research packages with "
          "`python -m backend.capability_registry trust capabilities/research_web --key <key> --operator NAME` "
          "(and research_evidence), then re-seal the build controller's protected files")
    return 0


if __name__ == "__main__":
    sys.exit(main())
