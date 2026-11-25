"""Upgrade safety (Stage 12 §39): compare an installed approved manifest with a candidate version.

Every expansion (permissions, tools, risk, side effects, network hosts, credentials, trust needs) and every breaking
schema change is reported explicitly; the report hash is part of what the operator signs, so permission expansion
can never happen silently."""
from __future__ import annotations

from backend.contracts import canonical_hash

from .types import RISK_ORDER


def _breaking_input(old: dict, new: dict) -> list[str]:
    out = []
    added_required = sorted(set(new.get("required") or []) - set(old.get("required") or []))
    if added_required:
        out.append(f"new required inputs {added_required}")
    op, np_ = old.get("properties") or {}, new.get("properties") or {}
    for k in sorted(set(op) & set(np_)):
        if isinstance(op[k], dict) and isinstance(np_[k], dict) and op[k].get("type") != np_[k].get("type"):
            out.append(f"input '{k}' type {op[k].get('type')} -> {np_[k].get('type')}")
    removed = sorted(set(op) - set(np_))
    if removed:
        out.append(f"inputs removed {removed}")
    return out


def _breaking_output(old: dict, new: dict) -> list[str]:
    out = []
    op, np_ = old.get("properties") or {}, new.get("properties") or {}
    removed = sorted(set(op) - set(np_))
    if removed:
        out.append(f"output fields removed {removed}")
    dropped_required = sorted(set(old.get("required") or []) - set(new.get("required") or []))
    if dropped_required:
        out.append(f"output fields no longer guaranteed {dropped_required}")
    for k in sorted(set(op) & set(np_)):
        if isinstance(op[k], dict) and isinstance(np_[k], dict) and op[k].get("type") != np_[k].get("type"):
            out.append(f"output '{k}' type {op[k].get('type')} -> {np_[k].get('type')}")
    return out


def compare(old: dict, new: dict) -> dict:
    ot = {t["name"]: t for t in old.get("tools") or []}
    nt = {t["name"]: t for t in new.get("tools") or []}
    perms_added = sorted(set(new.get("permissions_requested") or []) - set(old.get("permissions_requested") or []))
    perms_removed = sorted(set(old.get("permissions_requested") or []) - set(new.get("permissions_requested") or []))
    risk_increases, effect_changes, breaking, tool_perm_added = {}, {}, {}, {}
    for n in sorted(set(ot) & set(nt)):
        a, b = ot[n], nt[n]
        if RISK_ORDER[b["risk"]] > RISK_ORDER[a["risk"]]:
            risk_increases[n] = f"{a['risk']} -> {b['risk']}"
        if a["side_effect"] != b["side_effect"]:
            effect_changes[n] = f"{a['side_effect']} -> {b['side_effect']}"
        extra = sorted(set(b["permissions"]) - set(a["permissions"]))
        if extra:
            tool_perm_added[n] = extra
        br = _breaking_input(a["input_schema"], b["input_schema"]) + _breaking_output(a["output_schema"], b["output_schema"])
        if br:
            breaking[n] = br
    hosts_added = sorted(set(new["network_requirements"].get("hosts") or []) -
                         set(old["network_requirements"].get("hosts") or []))
    oc = {c["name"] for c in old.get("credential_requirements") or []}
    creds_added = sorted(c["name"] for c in new.get("credential_requirements") or [] if c["name"] not in oc)
    report = {
        "from_version": old["version"], "to_version": new["version"],
        "permissions_added": perms_added, "permissions_removed": perms_removed,
        "tool_permissions_added": tool_perm_added,
        "tools_added": sorted(set(nt) - set(ot)), "tools_removed": sorted(set(ot) - set(nt)),
        "risk_increases": risk_increases, "side_effect_changes": effect_changes,
        "network_hosts_added": hosts_added, "credentials_added": creds_added,
        "breaking_schema_changes": breaking,
        "execution_mode_changed": old["execution_mode"] != new["execution_mode"],
        "type_changed": old["integration_type"] != new["integration_type"],
    }
    report["expands_authority"] = bool(perms_added or tool_perm_added or report["tools_added"] or risk_increases
                                       or effect_changes or hosts_added or creds_added
                                       or report["execution_mode_changed"])
    report["breaking"] = bool(breaking or report["tools_removed"])
    report["report_hash"] = canonical_hash({k: v for k, v in report.items() if k != "report_hash"})
    return report
