"""Permanent numbered capability and tool identifiers (Stage 01A §5).

Capability number = the requirement-register number (CAP-04 -> 004). Tool numbers 'CCC.TTT' are assigned by the
operator in configuration/tool_numbers.yaml and recorded in the database (control.tool_identifiers) the first time
they are seen. From then on a number can never point at a different tool, a tool can never change number, and a
number can only leave the file by being marked retired — retired numbers are never reassigned."""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import yaml

NUMBER_RE = re.compile(r"^(0[0-3][0-9]|040)\.(\d{3})$")


class NumberingError(ValueError):
    pass


class _UniqueKeyLoader(yaml.SafeLoader):
    pass


def _construct_mapping(loader, node, deep=False):
    seen = set()
    for key_node, _ in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if key in seen:
            raise NumberingError(f"duplicate identifier {key!r} in tool numbering file")
        seen.add(key)
    return yaml.SafeLoader.construct_mapping(loader, node, deep)


_UniqueKeyLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _construct_mapping)


@dataclass(frozen=True)
class NumberEntry:
    number: str
    tool: str
    retired: bool


def capability_number(requirement: str | None) -> str | None:
    if not requirement:
        return None
    m = re.fullmatch(r"CAP-(\d{2})", requirement)
    return f"{int(m.group(1)):03d}" if m else None


def load_numbers(path: Path) -> dict[str, NumberEntry]:
    """Parse and validate the numbering file. Raises NumberingError on duplicates or malformed entries."""
    if not path.is_file():
        return {}
    data = yaml.load(path.read_text(encoding="utf-8"), Loader=_UniqueKeyLoader) or {}
    out: dict[str, NumberEntry] = {}
    by_tool: dict[str, str] = {}
    for number, e in (data.get("tools") or {}).items():
        number = str(number)
        if not NUMBER_RE.match(number) or number.endswith(".000"):
            raise NumberingError(f"invalid tool number {number!r} (expected CCC.TTT, CCC 001-040, TTT 001-999)")
        if not isinstance(e, dict) or not isinstance(e.get("tool"), str):
            raise NumberingError(f"{number}: entry must name its tool")
        if e["tool"] in by_tool:
            raise NumberingError(f"duplicate tool identifier: {e['tool']} has numbers {by_tool[e['tool']]} and {number}")
        by_tool[e["tool"]] = number
        out[number] = NumberEntry(number, e["tool"], bool(e.get("retired", False)))
    return out


def reconcile_with_store(entries: dict[str, NumberEntry], conn, namespace: str = "default") -> list[str]:
    """Enforce permanence against the persisted identifier table. Returns problems (empty = consistent).
    New numbers are recorded; nothing is ever deleted or re-pointed."""
    problems = []
    stored = {r["number"]: r for r in conn.execute(
        "SELECT number, tool_string, retired FROM control.tool_identifiers WHERE namespace = %s", (namespace,)).fetchall()}
    stored_by_tool = {r["tool_string"]: n for n, r in stored.items()}
    for n, r in stored.items():
        e = entries.get(n)
        if e is None:
            problems.append(f"tool number {n} ({r['tool_string']}) disappeared from the numbering file; "
                            "numbers may only be retired, never removed")
        elif e.tool != r["tool_string"]:
            problems.append(f"tool number {n} would be reassigned from {r['tool_string']} to {e.tool}")
        elif r["retired"] and not e.retired:
            problems.append(f"tool number {n} was retired and cannot be reactivated")
    for n, e in entries.items():
        if e.tool in stored_by_tool and stored_by_tool[e.tool] != n:
            problems.append(f"tool {e.tool} already has permanent number {stored_by_tool[e.tool]}, not {n}")
    if problems:
        return problems
    for n, e in entries.items():
        if n not in stored:
            conn.execute("INSERT INTO control.tool_identifiers (namespace, number, tool_string, retired) "
                         "VALUES (%s,%s,%s,%s)", (namespace, n, e.tool, e.retired))
        elif e.retired and not stored[n]["retired"]:
            conn.execute("UPDATE control.tool_identifiers SET retired = true, retired_at = now() "
                         "WHERE namespace = %s AND number = %s", (namespace, n))
    return []
