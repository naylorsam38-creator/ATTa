"""The repair policy: which existing repair action may run for which failure, and on what terms.

A policy is a versioned list of exact rules:

    {"failure_key": "v2|qualify|1 INSTALLED:NOT_EXECUTABLE", "action": "chmod_launcher", "decision": "AUTO",
     "why": "launcher mode bit only; restores nothing else"}

  failure_key  ATTa's own FailureKey (failure_keys.py): v<version>|<layer>|<strong key>. Exact match only: no
               patterns, no wildcards. A key that normalises to UNKNOWN_FAILURE can never be given AUTO or
               APPROVAL_REQUIRED (it would match unrelated errors), and loading such a policy fails.
  action       the name of an action registered in repair_actions.py. The gate never runs anything else.
  decision     one of the record diagnostic's closed set (record_diagnostic.ALLOWED_ACTIONS):
                 AUTO               run it
                 APPROVAL_REQUIRED  run it only with a recorded, unexpired approval for this remediation
                 RETRY_ONLY         the only permitted response is re-running the layer: the action is refused
                 ESCALATE           a person decides: refused
                 NO_ACTION          nothing may be done on this evidence: refused

A pair no rule names is ESCALATE. An unreadable, malformed or unknown-version policy file fails the load:
the gate then treats every repair as unauthorized (enforce mode) rather than falling back to "allow".

The default location is <APP_BUILDER_ROOT>/state/control/repair_policy.json (APP_BUILDER_CONTROL_POLICY
overrides). With no file, the policy is empty: in enforce mode nothing runs until a person writes rules.
`python3 -m atta_control policy scaffold` proposes rules from what observe mode recorded, all as
APPROVAL_REQUIRED; a person promotes the ones they trust to AUTO."""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping

import failure_keys
from record_diagnostic import ALLOWED_ACTIONS

SCHEMA = "ATTA_REPAIR_POLICY.v1"
ACTIONS = tuple(sorted(ALLOWED_ACTIONS))
EXECUTING = frozenset({"AUTO", "APPROVAL_REQUIRED"})


class PolicyError(ValueError):
    """The policy file cannot be trusted as written. Carries every problem found, not just the first."""
    def __init__(self, problems: list[str]):
        self.problems = problems
        super().__init__("; ".join(problems))


@dataclass(frozen=True)
class PolicyDecision:
    action: str
    policy_version: str
    reason: str
    rule: Mapping[str, Any] | None = None


@dataclass(frozen=True)
class RepairPolicy:
    version: str
    rules: tuple[Mapping[str, Any], ...] = ()
    source: str = "<memory>"

    def __post_init__(self) -> None:
        problems = validate_rules(self.version, self.rules)
        if problems:
            raise PolicyError(problems)

    @property
    def mappings(self) -> dict[tuple[str, str], str]:
        return {(r["failure_key"], r["action"]): r["decision"] for r in self.rules}

    def decide(self, failure_key: str, action: str) -> PolicyDecision:
        for r in self.rules:
            if r["failure_key"] == failure_key and r["action"] == action:
                return PolicyDecision(r["decision"], self.version, f"explicit rule: {r.get('why') or 'no reason given'}", r)
        return PolicyDecision("ESCALATE", self.version, "no explicit rule for this failure key and action")

    def to_dict(self) -> dict[str, Any]:
        return {"schema": SCHEMA, "version": self.version, "rules": [dict(r) for r in self.rules]}


def _known_actions() -> set[str] | None:
    """Names registered in repair_actions.py, or None when it can't be imported here (then names are not
    checked at load; the gate still refuses anything repair_actions.run() does not know)."""
    try:
        import repair_actions
        return set(repair_actions.ACTIONS)
    except Exception:
        return None


def validate_rules(version: Any, rules: Iterable[Mapping[str, Any]]) -> list[str]:
    problems: list[str] = []
    if not isinstance(version, str) or not version.strip():
        problems.append("policy version must be a non-empty string")
    known = _known_actions()
    seen: set[tuple[str, str]] = set()
    for i, r in enumerate(rules):
        where = f"rule {i + 1}"
        if not isinstance(r, Mapping):
            problems.append(f"{where}: not an object"); continue
        extra = set(r) - {"failure_key", "action", "decision", "why"}
        if extra:
            problems.append(f"{where}: unknown field(s) {sorted(extra)}")
        fk, act, dec = r.get("failure_key"), r.get("action"), r.get("decision")
        parsed = failure_keys.parse(fk) if isinstance(fk, str) else None
        if parsed is None:
            problems.append(f"{where}: failure_key {fk!r} is not a FailureKey this code reads "
                            f"(v{','.join(map(str, failure_keys.KNOWN_VERSIONS))}|layer|key)")
        elif parsed["key"] == failure_keys.UNKNOWN and dec in EXECUTING:
            problems.append(f"{where}: {dec} on UNKNOWN_FAILURE would authorize repairs for unrelated errors")
        if not isinstance(act, str) or not act:
            problems.append(f"{where}: action must be a repair action name")
        elif known is not None and act not in known:
            problems.append(f"{where}: action {act!r} is not registered in repair_actions.py")
        if dec not in ALLOWED_ACTIONS:
            problems.append(f"{where}: decision {dec!r} is not one of {', '.join(ACTIONS)}")
        key = (fk, act)
        if key in seen:
            problems.append(f"{where}: duplicate rule for {fk} / {act}")
        seen.add(key)
    return problems


def policy_path() -> Path:
    env = os.environ.get("APP_BUILDER_CONTROL_POLICY")
    if env:
        return Path(env)
    return Path(os.environ.get("APP_BUILDER_ROOT", "/srv/app-builder")) / "state" / "control" / "repair_policy.json"


def empty_policy() -> RepairPolicy:
    return RepairPolicy(version="empty", rules=(), source="<no policy file>")


def load_policy(path: Path | str | None = None) -> RepairPolicy:
    """The policy at `path` (default policy_path()). No file = the empty policy. Anything else wrong raises
    PolicyError: the caller decides what an untrusted policy means (the gate: nothing is authorized)."""
    p = Path(path) if path is not None else policy_path()
    if not p.exists():
        return empty_policy()
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise PolicyError([f"{p}: unreadable ({type(e).__name__}: {e})"]) from None
    if not isinstance(raw, dict):
        raise PolicyError([f"{p}: top level must be an object"])
    if raw.get("schema") != SCHEMA:
        raise PolicyError([f"{p}: schema {raw.get('schema')!r}, this code reads {SCHEMA}"])
    rules = raw.get("rules")
    if not isinstance(rules, list):
        raise PolicyError([f"{p}: rules must be a list"])
    return RepairPolicy(version=raw.get("version"), rules=tuple(rules), source=str(p))


def save_policy(policy: RepairPolicy, path: Path | str) -> Path:
    """Write-then-rename, mode 600. Never called by the gate: only by a person's command."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(json.dumps(policy.to_dict(), indent=2, sort_keys=True) + "\n")
    os.replace(tmp, p)
    return p


def scaffold(audit_events: Iterable[Mapping[str, Any]], version: str, existing: RepairPolicy | None = None) -> RepairPolicy:
    """A proposed policy from what the gate saw in observe mode: one APPROVAL_REQUIRED rule per
    (failure key, action) that actually ran and returned without raising, and whose failure key is strong.
    Existing rules are kept as they are (a person's decision is never overwritten)."""
    audit_events = list(audit_events)   # read twice below; a generator would be empty the second time
    rules = [dict(r) for r in (existing.rules if existing else ())]
    have = {(r["failure_key"], r["action"]) for r in rules}
    returned = {e.get("remediation_id") for e in audit_events if e.get("event") == "REPAIR_RETURNED"}
    for e in audit_events:
        if e.get("event") != "REPAIR_AUTHORIZATION" or e.get("remediation_id") not in returned:
            continue
        fk, act = e.get("failure_key"), e.get("fix_name")
        if not isinstance(fk, str) or failure_keys.is_unknown(fk) or (fk, act) in have:
            continue
        have.add((fk, act))
        rules.append({"failure_key": fk, "action": act, "decision": "APPROVAL_REQUIRED",
                      "why": "proposed from observe-mode audit: ran and returned; promote to AUTO only after review"})
    return RepairPolicy(version=version, rules=tuple(rules), source="<scaffold>")
