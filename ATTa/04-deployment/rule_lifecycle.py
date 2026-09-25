#!/usr/bin/env python3
"""
rule_lifecycle.py — the trust layer over everything the self-healer LEARNS.

v109 learns two kinds of rule and then trusts them forever:
  state/maintenance/learned_fixes.json   verified LLM fixes, replayed by known_fixes.py (tier 1)
  state/runner/learned_rules.json        failure patterns the LLM taught app_runner.py's diagnose()
Both are checked FIRST, before the hand-written rules, and nothing ever counted whether they still work.

This module gives every learned rule a record and a state. Nothing else changes: the same files,
the same shape, extra keys. A rule that was never counted is simply a candidate with zero counts.

  fired    the rule matched a failure
  held     its fix was applied and the layer then PASSED
  failed   its fix was applied and the layer still FAILED

  pending     just learned. Does NOT run. Becomes a candidate only when a full-catalogue run that
              STARTED after it was learned has COMPLETED. A single-app rerun never activates it
              (pipeline.qualify(only=...) does not call after_full_run) — there is no back door.
  candidate   activated, on probation. Runs (a rule no one runs is never tested).
  confirmed   held at least PROMOTE_MIN_HELD times AND a full-catalogue run since it was learned
              ended with no regression. This is the owner's locked rule: nothing is "fixed" until
              a full run proves it.
  degraded    confirmed, but its confidence has dropped below DEGRADE_BELOW. Still runs, shown
              on the report so it is watched. Recovers to confirmed when confidence climbs back.
  retired     failed RETIRE_AFTER_FAILED times in a row with no hold in between. Moved out of the
              live file into retired_fixes.json with its whole record, so it never runs again and
              the failure it covered drops back to the LLM tier. Never deleted: the record is the
              evidence.

Regression suspects: when a full run flags REGRESSION, the checklist names every learned rule
ACTIVATED or promoted since the last CLEAN full run. A pending rule never ran, so it is never a
suspect. That is the list to read first.

Key versions (v111a): a learned fix carries `failure_key` (failure_keys.py). One whose version this
code does not understand is refused — never matched, shown here as REFUSED — not migrated.

All writes: file lock -> read -> change -> temp file -> rename -> unlock. All reads of the stores go
through load(), also under the lock. Parallel workers cannot overwrite each other's counts or read
mid-update.
"""
from __future__ import annotations
import fcntl, json, os, tempfile, threading, time
from pathlib import Path

# ===================== CONFIG — edit here, nothing below needs reading =====================
# A candidate needs this many HOLDS before it can be confirmed. Lower = trusts new rules sooner.
PROMOTE_MIN_HELD = int(os.environ.get("APP_BUILDER_RULE_PROMOTE_HELD", "2"))
# This many FAILED in a row, with no hold between, retires a rule (candidate or confirmed).
# Lower = drops bad rules faster but a flaky app can take out a good rule; higher = more patience.
RETIRE_AFTER_FAILED = int(os.environ.get("APP_BUILDER_RULE_RETIRE_FAILED", "3"))
# A confirmed rule whose confidence (held / (held+failed)) drops under this is shown as DEGRADED.
# It still runs. Raise to be stricter about what counts as healthy.
DEGRADE_BELOW = float(os.environ.get("APP_BUILDER_RULE_DEGRADE_BELOW", "0.5"))
# Where things live. ROOT is the same APP_BUILDER_ROOT every other module uses.
ROOT = Path(os.environ.get("APP_BUILDER_ROOT", "/srv/app-builder"))
LEARNED_FIXES = ROOT / "state" / "maintenance" / "learned_fixes.json"    # same file known_fixes.py uses
LEARNED_RULES = ROOT / "state" / "runner" / "learned_rules.json"         # same file app_runner.py uses
RETIRED_FILE = ROOT / "state" / "maintenance" / "retired_fixes.json"     # retired rules, both kinds, with full record
RUNS_FILE = ROOT / "state" / "maintenance" / "rule_runs.json"            # when the last CLEAN full run was
HEALTH_FILE = ROOT / "state" / "checklists" / "rule_health.md"           # the report, rewritten every full run
LOCK_FILE = ROOT / "state" / "maintenance" / ".rule_lifecycle.lock"
# ==========================================================================================

STATES = ("pending", "candidate", "confirmed", "degraded", "retired")
_TLOCK = threading.RLock()
_DEPTH = [0]      # nesting depth of _Locked in the holding thread (guarded by _TLOCK)
_FD = [None]


# ---------------------------------------------------------------- locked, atomic file access
class _Locked:
    """Process-wide (flock) + thread-wide (RLock) exclusive section. Re-entrant per thread: the flock is
    taken once at the outermost entry and released at the outermost exit, so a locked writer may call a
    locked reader without deadlocking on its own lock."""
    def __enter__(self):
        _TLOCK.acquire()
        if _DEPTH[0] == 0:
            LOCK_FILE.parent.mkdir(parents=True, exist_ok=True)
            _FD[0] = os.open(LOCK_FILE, os.O_RDWR | os.O_CREAT, 0o644)
            fcntl.flock(_FD[0], fcntl.LOCK_EX)
        _DEPTH[0] += 1
        return self
    def __exit__(self, *a):
        try:
            _DEPTH[0] -= 1
            if _DEPTH[0] == 0:
                fcntl.flock(_FD[0], fcntl.LOCK_UN); os.close(_FD[0]); _FD[0] = None
        finally:
            _TLOCK.release()


def load(path: Path) -> list[dict]:
    """Read a rule store UNDER THE LOCK. Every reader of the four stores comes through here, so a read can
    never overlap a writer's read-modify-write and observe a state that is about to be replaced."""
    with _Locked():
        try:
            d = json.loads(Path(path).read_text())
            return d if isinstance(d, list) else []
        except (OSError, ValueError):
            return []


def save(path: Path, rows: list[dict]) -> None:
    """Atomic: written to a temp file beside the target, then renamed over it. Call inside _Locked
    for read-modify-write; on its own it is still atomic against readers."""
    path = Path(path); path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    with os.fdopen(fd, "w") as f:
        json.dump(rows, f, indent=2); f.write("\n")
    os.replace(tmp, path)


def _save_obj(path: Path, obj) -> None:
    """Same atomic write, for a JSON object rather than a list of rules."""
    path = Path(path); path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    with os.fdopen(fd, "w") as f:
        json.dump(obj, f, indent=2); f.write("\n")
    os.replace(tmp, path)


# ---------------------------------------------------------------- the record on each rule
def new_fields() -> dict:
    """Lifecycle keys a freshly learned rule gets. Added to the entry known_fixes.learn / add_runner_rule write."""
    return {"state": "pending", "fired": 0, "held": 0, "failed": 0, "streak_failed": 0, "confidence": 0.0,
            "learned_at": time.time(), "activated_at": None, "confirmed_at": None, "last_held": None,
            "last_failed": None, "last_failure": None, "clean_runs": 0}


def _ensure(e: dict) -> dict:
    """A pre-lifecycle entry (written by v109) gets the record added in place. It was already running,
    so it is a CANDIDATE (not pending) and counts as activated when it was learned."""
    for k, v in new_fields().items():
        if k not in e:
            if k == "state": e[k] = "candidate"
            elif k == "learned_at": e[k] = e.get("learned_at") or v
            else: e[k] = v
    if e["state"] != "pending" and not e.get("activated_at"):
        e["activated_at"] = e.get("learned_at")
    return e


def _live_since(e: dict) -> float:
    """When this rule started RUNNING (activation), or 0 for a pending rule that never has."""
    if e.get("state") == "pending":
        return 0
    return e.get("activated_at") or e.get("learned_at") or 0


def _confidence(e: dict) -> float:
    n = e["held"] + e["failed"]
    return round(e["held"] / n, 3) if n else 0.0


def _restate(e: dict) -> None:
    e["confidence"] = _confidence(e)
    if e["state"] == "confirmed" and e["confidence"] < DEGRADE_BELOW:
        e["state"] = "degraded"
    elif e["state"] == "degraded" and e["confidence"] >= DEGRADE_BELOW:
        e["state"] = "confirmed"


def record(path: Path, rule_id: str, event: str, evidence: dict | None = None) -> dict | None:
    """Count one event (fired | held | failed) against one rule in one store. Returns the entry as it
    now stands, or None if the rule is not in that store (a built-in rule: nothing to count)."""
    assert event in ("fired", "held", "failed"), event
    with _Locked():
        rows = load(path)
        e = next((r for r in rows if r.get("id") == rule_id), None)
        if e is None:
            return None
        _ensure(e)
        e[event] += 1
        now = time.time()
        if event == "held":
            e["streak_failed"] = 0; e["last_held"] = now
        elif event == "failed":
            e["streak_failed"] += 1; e["last_failed"] = now
            if evidence:
                e["last_failure"] = {k: (str(v)[:400] if v is not None else None) for k, v in evidence.items()}
        _restate(e)
        if event == "failed" and e["streak_failed"] >= RETIRE_AFTER_FAILED:
            _retire(rows, e, path, f"failed {e['streak_failed']} times in a row with no hold")
        save(path, rows)
        return dict(e)


def _retire(rows: list[dict], e: dict, path: Path, why: str) -> None:
    e["state"] = "retired"; e["retired_at"] = time.time(); e["retired_why"] = why
    e["retired_from"] = str(path)
    rows.remove(e)
    ret = load(RETIRED_FILE); ret.append(e); save(RETIRED_FILE, ret)


# ---------------------------------------------------------------- full-run bookkeeping
def after_full_run(run_started: float, regressions: list[str], complete: bool) -> dict:
    """Called by the pipeline once per full-catalogue run, after regressions are known. Never by a
    single-app rerun (pipeline.qualify(only=...)).
    Any COMPLETE full run: pending rules learned before this run started are ACTIVATED (-> candidate).
    Clean run (complete, no regression) also: candidates that have held enough and were activated before
    this run started are CONFIRMED; every live rule's clean_runs goes up; the clean-run clock is set.
    Regression: nothing is promoted; returns the suspects = rules activated or promoted since the last
    clean run. Rules activated just now never ran and are not suspects."""
    now = time.time()
    with _Locked():
        runs = {}
        try: runs = json.loads(RUNS_FILE.read_text())
        except (OSError, ValueError): pass
        last_clean = runs.get("last_clean_run") or 0
        promoted, suspects, activated = [], [], []
        for path in (LEARNED_FIXES, LEARNED_RULES):
            rows = load(path); changed = False
            for e in rows:
                _ensure(e)
                if complete and e["state"] == "pending" and (e.get("learned_at") or 0) < run_started:
                    e["state"] = "candidate"; e["activated_at"] = now; changed = True
                    activated.append(_summary(e, path))
                    continue   # activated after this run: it did not run in it
                if regressions or not complete:
                    if _live_since(e) > last_clean or (e.get("confirmed_at") or 0) > last_clean:
                        suspects.append(_summary(e, path))
                    continue
                if e["state"] == "pending":
                    continue
                e["clean_runs"] += 1; changed = True
                if e["state"] == "candidate" and e["held"] >= PROMOTE_MIN_HELD and _live_since(e) < run_started:
                    e["state"] = "confirmed"; e["confirmed_at"] = now; _restate(e)
                    promoted.append(_summary(e, path))
            if changed: save(path, rows)
        if complete and not regressions:
            runs["last_clean_run"] = now
        runs["last_run"] = now; runs["last_run_regressions"] = list(regressions)
        _save_obj(RUNS_FILE, runs)
    out = {"promoted": promoted, "suspects": suspects, "activated": activated}
    try:
        HEALTH_FILE.parent.mkdir(parents=True, exist_ok=True)
        HEALTH_FILE.write_text(health_text(out))
    except OSError:
        pass
    return out


def _summary(e: dict, path: Path) -> str:
    kind = "fix" if Path(path) == LEARNED_FIXES else "runner-rule"
    return f"{e.get('id')} ({kind}, {e.get('state')}, held {e.get('held', 0)}/failed {e.get('failed', 0)})"


# ---------------------------------------------------------------- the report
def health_rows() -> list[dict]:
    rows = []
    for path, kind in ((LEARNED_FIXES, "fix"), (LEARNED_RULES, "runner-rule")):
        for e in load(path):
            _ensure(e); rows.append({**e, "kind": kind})
    for e in load(RETIRED_FILE):
        rows.append({**e, "kind": "fix" if e.get("retired_from", "").endswith("learned_fixes.json") else "runner-rule"})
    order = {"degraded": 0, "pending": 1, "candidate": 2, "confirmed": 3, "retired": 4}
    return sorted(rows, key=lambda e: (order.get(e.get("state"), 9), -(e.get("failed") or 0)))


def health_text(run: dict | None = None) -> str:
    rows = health_rows()
    def when(t): return time.strftime("%Y-%m-%d %H:%M", time.localtime(t)) if t else ""
    lines = ["# Learned-rule health", "", f"Generated {when(time.time())}. "
             f"{sum(r['state']=='confirmed' for r in rows)} confirmed, {sum(r['state']=='degraded' for r in rows)} degraded, "
             f"{sum(r['state']=='candidate' for r in rows)} candidate, {sum(r['state']=='pending' for r in rows)} pending, "
             f"{sum(r['state']=='retired' for r in rows)} retired.", ""]
    if run:
        lines += ["Activated this run (pending -> candidate, runs from next run): " + (", ".join(run.get("activated") or []) or "none"),
                  "Promoted this run: " + (", ".join(run["promoted"]) or "none"),
                  "Regression suspects (activated or promoted since the last clean run): " + (", ".join(run["suspects"]) or "none"), ""]
    lines += ["| Rule | Kind | State | Key | Confidence | Fired | Held | Failed | Streak | Last held | Last failed | Note |",
              "|---|---|---|---|---|---|---|---|---|---|---|---|"]
    import failure_keys
    for r in rows:
        refused = failure_keys.refused(r) if r["kind"] == "fix" and r["state"] != "retired" else None
        state = "REFUSED" if refused else r.get("state", "").upper()
        note = refused or r.get("retired_why") or (r.get("last_failure") or {}).get("detail") or r.get("note") or ""
        key = (failure_keys.for_rule(r) or r.get("failure_key") or "") if r["kind"] == "fix" else r.get("pattern", "")
        lines.append(f"| {r.get('id')} | {r['kind']} | {state} | {str(key)[:60].replace('|','/')} | {int((r.get('confidence') or 0)*100)}% | "
                     f"{r.get('fired',0)} | {r.get('held',0)} | {r.get('failed',0)} | {r.get('streak_failed',0)} | "
                     f"{when(r.get('last_held'))} | {when(r.get('last_failed'))} | {str(note)[:80].replace('|','/')} |")
    lines += ["", "Read: REFUSED, DEGRADED and RETIRED rows first. PENDING rules have not run yet. A rule failing on many different apps is a bad rule; "
              "a rule failing on one app is probably that app.", ""]
    return "\n".join(lines)


if __name__ == "__main__":
    print(health_text())
