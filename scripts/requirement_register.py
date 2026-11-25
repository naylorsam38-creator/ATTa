#!/usr/bin/env python3
"""Validate documentation/requirements/requirement_register.yaml and render MASTER_IMPLEMENTATION_REGISTER.md.

  python scripts/requirement_register.py check     # validate; exit 1 on problems or if the .md is stale
  python scripts/requirement_register.py render    # rewrite MASTER_IMPLEMENTATION_REGISTER.md from the YAML
"""
from __future__ import annotations

import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
REGISTER = ROOT / "documentation" / "requirements" / "requirement_register.yaml"
MASTER = ROOT / "MASTER_IMPLEMENTATION_REGISTER.md"
STATUSES = {"NOT_STARTED", "PARTIAL", "IMPLEMENTED", "VERIFIED", "BLOCKED"}
FIELDS = ("id", "title", "implementation_stage", "dependencies", "source_files", "interface_controls",
          "api_endpoints", "security_controls", "acceptance_tests", "status", "evidence")


def load() -> dict:
    return yaml.safe_load(REGISTER.read_text(encoding="utf-8"))


def validate(data: dict) -> list[str]:
    problems = []
    reqs = data.get("requirements", [])
    ids = [r.get("id") for r in reqs]
    expected = [f"CAP-{i:02d}" for i in range(1, 41)]
    if ids != expected:
        problems.append(f"register must list CAP-01..CAP-40 in order; missing={sorted(set(expected) - set(ids))} "
                        f"extra={sorted(set(ids) - set(expected))}")
    stages = {f"S{i:02d}" for i in range(1, 21)}
    for r in reqs:
        for f in FIELDS:
            if f not in r or r[f] in (None, "", [], {}):
                if f != "evidence" or r.get("status") in ("IMPLEMENTED", "VERIFIED", "PARTIAL"):
                    problems.append(f"{r.get('id')}: field '{f}' missing or empty")
        if r.get("status") not in STATUSES:
            problems.append(f"{r.get('id')}: invalid status {r.get('status')}")
        if r.get("implementation_stage") not in stages:
            problems.append(f"{r.get('id')}: implementation_stage must be S01..S20")
        for dep in r.get("dependencies", []) or []:
            if dep not in expected and dep != "FOUNDATION":
                problems.append(f"{r.get('id')}: unknown dependency {dep}")
        if r.get("status") in ("IMPLEMENTED", "VERIFIED"):
            for ev in r.get("evidence") or []:
                if not (ROOT / ev).exists():
                    problems.append(f"{r.get('id')}: evidence {ev} does not exist")
            if not r.get("evidence"):
                problems.append(f"{r.get('id')}: marked {r['status']} without evidence")
        for src in (r.get("source_files") or {}).get("implemented", []) or []:
            if not (ROOT / src).exists():
                problems.append(f"{r.get('id')}: implemented source {src} does not exist")
    return problems


def _cell(v) -> str:
    if isinstance(v, dict):
        return "<br>".join(f"**{k}:** " + ", ".join(f"`{x}`" for x in (vals or [])) for k, vals in v.items() if vals) or "—"
    if isinstance(v, list):
        return "<br>".join(str(x) for x in v) if v else "—"
    return str(v)


def render(data: dict) -> str:
    reqs = data["requirements"]
    counts = {s: sum(1 for r in reqs if r["status"] == s) for s in sorted(STATUSES)}
    out = ["# MASTER IMPLEMENTATION REGISTER", "",
           "Generated from `documentation/requirements/requirement_register.yaml` by "
           "`python scripts/requirement_register.py render`. Edit the YAML, not this file.", "",
           f"Register version: {data['version']} — updated {data['updated']} — current stage: {data['current_stage']}",
           "", "Status counts: " + ", ".join(f"{k} {v}" for k, v in counts.items()), "",
           "No capability is marked IMPLEMENTED or VERIFIED until its stage gate passes with recorded evidence.", "",
           "## Summary", "", "| ID | Capability | Stage | Depends on | Status |", "|---|---|---|---|---|"]
    for r in reqs:
        out.append(f"| {r['id']} | {r['title']} | {r['implementation_stage']} | "
                   f"{', '.join(r['dependencies'])} | **{r['status']}** |")
    out += ["", "## Detail", ""]
    for r in reqs:
        out += [f"### {r['id']} — {r['title']}", "", f"- **Implementation stage:** {r['implementation_stage']}",
                f"- **Status:** {r['status']}" + (f" — {r['status_note']}" if r.get("status_note") else ""),
                f"- **Dependencies:** {', '.join(r['dependencies'])}",
                f"- **Source files:** {_cell(r['source_files'])}",
                f"- **Interface controls:** {_cell(r['interface_controls'])}",
                f"- **API endpoints:** {_cell(r['api_endpoints'])}",
                f"- **Security controls:** {_cell(r['security_controls'])}",
                f"- **Acceptance tests:** {_cell(r['acceptance_tests'])}",
                f"- **Evidence:** {_cell(r.get('evidence') or ['none yet'])}", ""]
    return "\n".join(out)


def main(argv: list[str]) -> int:
    cmd = argv[1] if len(argv) > 1 else "check"
    data = load()
    problems = validate(data)
    if cmd == "render":
        if problems:
            print("\n".join(problems))
            return 1
        MASTER.write_text(render(data), encoding="utf-8")
        print(f"wrote {MASTER.relative_to(ROOT)}")
        return 0
    if MASTER.is_file() and MASTER.read_text(encoding="utf-8") != render(data):
        problems.append("MASTER_IMPLEMENTATION_REGISTER.md is out of date: run render")
    print("\n".join(problems) if problems else f"register valid: {len(data['requirements'])} requirements")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
