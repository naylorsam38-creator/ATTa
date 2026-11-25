"""Permission catalog loaded from configuration/permissions.yaml and validated."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml


class CatalogError(ValueError):
    pass


@dataclass(frozen=True)
class Catalog:
    permissions: dict[str, str]
    controller_granted: frozenset[str]
    approval_required_risks: frozenset[str]


def load_catalog(path: Path) -> Catalog:
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    perms = data.get("permissions") or {}
    if not perms:
        raise CatalogError(f"{path}: permissions are required")
    granted = set(data.get("controller_granted_permissions") or [])
    bad = sorted(p for p in granted if p not in perms or "*" in p)
    if bad:
        raise CatalogError(f"{path}: controller_granted_permissions not in the catalog: {bad}")
    return Catalog(permissions=dict(perms), controller_granted=frozenset(granted),
                   approval_required_risks=frozenset(data.get("approval_required_risks") or
                                                     ["medium", "high", "critical"]))
