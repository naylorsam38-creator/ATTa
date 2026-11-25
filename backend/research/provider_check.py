"""Search-provider configuration check (§8) — controlled integration of a real provider.

    python -m backend.research.provider_check [--config configuration/research.yaml]

Validates the research configuration offline (no network): schema, unique ids, supported kinds, endpoint URL
safety (the same planning-layer checks retrieval uses), and reports for each provider whether it is USABLE,
BLOCKED (dependency) or INVALID. Exit 0 when the configuration is valid (even with zero providers: then external
search is reported BLOCKED, never faked), 1 when it is invalid.

A LIVE check is deliberately not done here: a live query must run through the Stage 01A controller (trusted,
numbered, granted packages + operator-approved objective). Procedure: INSTALL.md "Live provider check"."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import yaml
from pydantic import ValidationError

from backend.settings import REPO_ROOT

from .config import ResearchConfig
from .errors import ResearchError
from .urls import precheck_url

SUPPORTED = {"searxng": "USABLE", "brave": "BLOCKED"}
BLOCK_REASON = {"brave": "needs an API key delivered by Stage 02 credential protection (BLOCKED_DEPENDENCY)"}


def check(path: Path) -> dict:
    report = {"config": str(path), "valid": True, "errors": [], "warnings": [], "providers": []}
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        cfg = ResearchConfig.model_validate(raw)
    except (OSError, yaml.YAMLError, ValidationError) as exc:
        report["valid"] = False
        report["errors"].append(f"configuration invalid: {exc}"[:2000])
        return report
    ids = [p.id for p in cfg.providers]
    dup = sorted({i for i in ids if ids.count(i) > 1})
    if dup:
        report["valid"] = False
        report["errors"].append(f"duplicate provider ids: {dup}")
    for p in cfg.providers:
        entry = {"id": p.id, "kind": p.kind, "enabled": p.enabled, "status": SUPPORTED.get(p.kind, "INVALID")}
        try:
            precheck_url(p.endpoint)
        except ResearchError as exc:
            entry["status"] = "INVALID"
            entry["reason"] = f"{exc.code.value}: {exc.message}"
            report["valid"] = False
            report["errors"].append(f"provider {p.id}: endpoint refused: {exc.message}")
        else:
            if p.endpoint.startswith("http://"):
                report["warnings"].append(f"provider {p.id}: endpoint is plain http; queries are visible on the network")
            if entry["status"] == "BLOCKED":
                entry["reason"] = BLOCK_REASON[p.kind]
        if not p.enabled:
            entry["status"] = "DISABLED"
        report["providers"].append(entry)
    if not any(e["status"] == "USABLE" for e in report["providers"]):
        report["warnings"].append("no usable provider: external search will report PROVIDER_UNAVAILABLE (BLOCKED)")
    report["network_mode_default"] = cfg.network_mode.value
    return report


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m backend.research.provider_check")
    ap.add_argument("--config", default=str(REPO_ROOT / "configuration" / "research.yaml"))
    a = ap.parse_args(argv)
    r = check(Path(a.config))
    print(json.dumps(r, indent=2))
    return 0 if r["valid"] else 1


if __name__ == "__main__":
    sys.exit(main())
