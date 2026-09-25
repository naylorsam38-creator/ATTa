#!/usr/bin/env python3
"""
maintenance.py — the self-healing layer. Runs when a build fails at any of the three layers:

  build     the pipeline itself failed          (build state FAILED)
  qualify   the six-stage watcher gate failed   (build state NOT_QUALIFIED)
  handoff   Coolify hasn't accepted it          (QUALIFIED, coolify not DISPATCHED after a few retries)

For every failure item, strictly cheapest first:

  1. SCRIPT      known_fixes.py: match against known, previously solved problems. No AI.
  2. ADAPTER     capability_adapter.py: is the broken piece one of the Capability adapter's own,
                 and can it repair it?
  3. LLM         llm_repair.py: Claude diagnoses with the real error context and uses the bounded
                 repair actions.
  4. HUMAN       alerts.py: only after 1–3 are spent, with the full chain of what was tried.

After a tier changes something, the failed layer is re-run to verify the fix: qualify and handoff
at once; build by requeuing the bundle, so the pipeline's next run is the check. If the failure is
still there, that item moves to the next tier. A verified LLM fix is saved as a known fix, so next
time tier 1 handles it with no LLM call.

Everything is recorded on the build record under `healing.<layer>`, which the owner sees on their
build page and admins see on /alerts.
"""
from __future__ import annotations
import re, time

import builds, known_fixes, capability_adapter, llm_repair, alerts, syntax_triage, repair_actions as ra, rule_lifecycle

# ===================== CONFIG — edit here, nothing below needs reading =====================
# Tier 3 when the LLM is off (APP_BUILDER_HEAL_LLM_PER_DAY=0 or no API key): the SCRIPT PROBE.
# For a failure no known fix matches, try each GENERAL repair for that layer, one per round, and verify.
# The first one that makes the failure go away is saved as a learned rule under the failure's key
# (its area, e.g. "1 INSTALLED:NOT_EXECUTABLE", never the app's name), so the same kind of failure on
# ANY app is fixed by tier 1 next time. Learned rules go through rule_lifecycle.py like any other.
# Add a general action here (from repair_actions.py) and the probe starts trying it.
PROBE = {
    "qualify": [("reinstall_app_overlay", {"app": "{app}"}), ("reregister_target", {"app": "{app}"}),
                ("chmod_launcher", {"app": "{app}"}), ("start_proxy", {"app": "{app}"}), ("install_browser", {})],
    "handoff": [("lookup_coolify_uuid", {"app": "{app}"}), ("redispatch", {"build_id": "{build_id}"})],
    "build": [],
}
# Most healing rounds per build per layer (a round = try the next tier on every open item, then verify).
# Enough for tiers 1-2 plus every probe candidate.
MAX_ROUNDS = 3 + max(len(v) for v in PROBE.values())
# Coolify hand-off: start healing only after this many failed dispatch attempts (~15 min of plain retries at the defaults).
HANDOFF_HEAL_AFTER = 6
# ==========================================================================================

LAYERS = ("build", "qualify", "handoff")
TIER_NAMES = {1: "script", 2: "adapter", 3: "llm", 4: "human"}


# ---------------------------------------------------------------- what is failing
def _norm(text: str) -> str:
    """Stable key for a free-text error: paths, numbers and hex ids stripped."""
    t = re.sub(r"(/[\w.\-]+)+", "<path>", text or "unknown")
    t = re.sub(r"\b[0-9a-f]{8,}\b", "<id>", t)
    t = re.sub(r"\d+", "#", t)
    return t[:160]


def failures(rec: dict, layer: str) -> list[dict]:
    if layer == "build":
        if rec.get("state") != "FAILED":
            return []
        err = str(rec.get("error") or "unknown error")
        return [{"key": _norm(err), "detail": err[:2000]}]
    if layer == "qualify":
        if rec.get("state") not in ("NOT_QUALIFIED", "PARTIALLY_QUALIFIED"):
            return []
        res = rec.get("qualification_results")
        if not res:
            return [{"key": "TARGETS:NO_TARGETS", "detail": rec.get("error") or ""}]
        out = []
        for r in res:
            if r.get("broken_at"):
                st = r["broken_at"]; d = (r.get("stages") or {}).get(st, {})
                out.append({"key": f"{st}:{r.get('code')}", "app": r.get("app"), "detail": str(d.get("detail") or "")[:2000]})
            else:
                c = (r.get("stages") or {}).get("6 CLEAN", {})
                if c.get("status") != "OK":
                    out.append({"key": f"6 CLEAN:{c.get('status', 'MISSING')}:{c.get('code', '')}", "app": r.get("app"),
                                "detail": str(c.get("detail") or "")[:2000]})
        return out
    if layer == "handoff":
        c = rec.get("coolify") or {}
        if rec.get("state") not in (builds.QUALIFIED, builds.PARTIALLY_QUALIFIED) or not c or c.get("status") == "DISPATCHED":
            return []
        if c.get("status") == "BLOCKED_NOT_CONFIGURED":
            return [{"key": "NOT_CONFIGURED", "detail": c.get("last_error", "")}]
        out = []
        for app, st in (c.get("apps") or {}).items():
            if st.get("status") == "UNMAPPED":
                out.append({"key": "UNMAPPED", "app": app, "detail": st.get("detail", "")})
            elif st.get("status") == "ERROR":
                detail = st.get("detail", "")
                out.append({"key": "ERROR:" + detail.split(":", 1)[0], "app": app, "detail": detail[:2000]})
        return out
    raise ValueError(layer)


def _sig(layer, it):
    return f"{layer}|{it['key']}|{it.get('app') or ''}"


# ---------------------------------------------------------------- tiers
def _run_actions(steps) -> tuple[bool, list[dict]]:
    done, ok = [], True
    for name, args in steps:
        try:
            out, err = ra.run(name, args), False
        except Exception as e:
            out, err, ok = f"{type(e).__name__}: {e}", True, False
        done.append({"name": name, "args": args, "result": str(out)[:1000], "error": err})
        if err:
            break
    return ok, done


def _probe(layer: str, it: dict, rec: dict, st: dict, e: dict) -> dict:
    """Tier 3 without the LLM: the next general repair for this layer that applies. `probe_more` tells
    heal() to come back to tier 3 next round if this one doesn't hold."""
    e = {**e, "tier_name": "probe"}
    ctx = {"app": it.get("app") or "", "build_id": rec["id"]}
    cands = PROBE.get(layer, [])
    i = st.get("probe_i", 0)
    tried = []
    while i < len(cands):
        name, tmpl = cands[i]; i += 1
        args = {k: (v.format(**ctx) if isinstance(v, str) else v) for k, v in tmpl.items()}
        if any(v == "" for v in args.values()):
            continue   # needs an app and this failure has none
        ok, done = _run_actions([(name, args)])
        tried += done
        if ok:
            st["probe_i"] = i
            return {**e, "outcome": "APPLIED", "actions": done, "probe_more": i < len(cands),
                    "note": f"probe: general repair {name} for {it.get('key')}"}
    st["probe_i"] = i
    return {**e, "outcome": "NO_FIX", "actions": tried, "note": "probe: no general repair left for this layer"}


def _tier(n: int, layer: str, it: dict, rec: dict, chain: list[dict], st: dict | None = None) -> dict:
    """One tier's attempt at one item. Returns an entry for the chain."""
    e = {"tier": n, "tier_name": TIER_NAMES[n], "item": it, "at": time.time()}
    if n == 1:
        fix = known_fixes.match(layer, it, rec["id"])
        if not fix:
            return {**e, "outcome": "NOT_RECOGNISED"}
        e["rule"] = fix["rule"]
        if fix.get("human"):
            return {**e, "outcome": "NEEDS_HUMAN", "why": fix["human"]}
        if fix.get("retry"):
            return {**e, "outcome": "APPLIED", "actions": [], "note": "known transient failure: run the layer again"}
        ok, done = _run_actions(fix["actions"])
        return {**e, "outcome": "APPLIED" if ok else "FIX_FAILED", "actions": done}
    if n == 2:
        p = capability_adapter.plan(it)
        if not p:
            return {**e, "outcome": "NOT_MINE"}
        if not p["actions"]:
            return {**e, "outcome": "NOT_REPAIRABLE", "findings": p["findings"]}
        ok, done = _run_actions(p["actions"])
        return {**e, "outcome": "APPLIED" if ok else "FIX_FAILED", "findings": p["findings"], "actions": done}
    if n == 3:
        if not llm_repair.available()[0]:
            return _probe(layer, it, rec, st if st is not None else {}, e)
        r = llm_repair.attempt(layer, it, rec, chain)
        return {**e, "outcome": "APPLIED" if r["applied"] else "NO_FIX", "actions": r["actions"], "note": r["note"]}
    raise ValueError(n)


# ---------------------------------------------------------------- orchestration
def _state(rec, layer):
    h = (rec.get("healing") or {}).get(layer) or {"status": "IDLE", "rounds": 0, "items": {}, "chain": [], "alerted": []}
    return h


def _save(bid, layer, h):
    rec = builds.get(bid) or {}
    healing = rec.get("healing") or {}
    healing[layer] = h
    builds.update(bid, healing=healing)


def _verify(bid, layer) -> bool | None:
    """Re-run the failed layer. None = verification happens later (build layer: the requeued run)."""
    if layer == "build":
        ra.run("requeue_build", {"build_id": bid})
        builds.update(bid, state="HEALING_REQUEUED")
        return None
    if layer == "qualify":
        import pipeline
        return pipeline.requalify(bid)
    if layer == "handoff":
        import coolify_handoff
        return coolify_handoff.dispatch(bid).get("status") == "DISPATCHED"


def _count_learned(h, event: str, evidence: dict | None = None) -> None:
    """Every item whose last applied fix came from a LEARNED tier-1 rule: count the verification result
    against that rule. Built-in rules (no `learned.` prefix) are not counted; they are hand-written."""
    for st in h["items"].values():
        last = st.get("last_applied") or {}
        rule = last.get("rule") or ""
        if last.get("tier") == 1 and rule.startswith("learned."):
            rule_lifecycle.record(known_fixes.LEARNED_FILE, rule, event, evidence)


def _learn_item(bid, layer, st):
    """An item whose fix came from tier 3 (LLM or script probe) becomes a tier-1 known fix."""
    last = st.get("last_applied")
    if not (last and last.get("tier") == 3 and last.get("actions")) or "learned" in st:
        return
    # Content-specific edits (a patch to one file) are not replayed on other builds.
    if any(not ra.ACTIONS.get(a["name"], {}).get("learnable", True) for a in last["actions"]):
        st["learned"] = False
        return
    it = last["item"]
    rid = known_fixes.learn(layer, it["key"], it.get("app"), bid,
                            [a for a in last["actions"] if not a.get("error")], last.get("note", ""))
    # None = the failure's key was UNKNOWN_FAILURE, too weak to replay: repaired, not remembered.
    st["learned"] = rid is not None
    st["learned_rule"] = rid


def _succeeded(bid, layer, h):
    h["status"] = "HEALED"; h["healed_at"] = time.time()
    _count_learned(h, "held")
    for st in h["items"].values():
        _learn_item(bid, layer, st)
    _save(bid, layer, h)


def _escalate(bid, layer, h, items):
    new = [it for it in items if _sig(layer, it) not in h["alerted"]]
    if not new:
        return
    for it in new:
        why = next((c.get("why") for c in reversed(h["chain"]) if c.get("item") == it and c.get("why")), None)
        if why:
            it["why"] = why
    a = alerts.raise_alert(bid, layer, new, h["chain"])
    h["alerted"] += [_sig(layer, it) for it in new]
    h["status"] = "ESCALATED"
    h["chain"].append({"tier": 4, "tier_name": "human", "at": time.time(), "outcome": "ALERTED",
                       "items": new, "delivered": a["delivered"]})


def heal(bid: str, layer: str) -> str:
    """Run escalation for one build at one layer. Returns the resulting healing status."""
    rec = builds.get(bid)
    if not rec:
        return "NO_BUILD"
    h = _state(rec, layer)
    while True:
        items = failures(rec, layer)
        # Triage: attach exact file:line syntax errors (or rule syntax out) before any tier runs.
        items = [syntax_triage.enrich(layer, it) for it in items]
        if not items:
            if h["status"] not in ("IDLE", "HEALED"):
                _succeeded(bid, layer, h)
            return h["status"]
        if h["rounds"] >= MAX_ROUNDS:
            _escalate(bid, layer, h, items); _save(bid, layer, h)
            return h["status"]
        h["rounds"] += 1; h["status"] = "HEALING"
        applied, exhausted = False, []
        for it in items:
            st = h["items"].setdefault(_sig(layer, it), {"next_tier": 1})
            done_this_round = False
            while st["next_tier"] <= 3:
                n = st["next_tier"]; st["next_tier"] += 1
                entry = _tier(n, layer, it, rec, h["chain"], st)
                h["chain"].append(entry)
                if entry.get("probe_more"):
                    st["next_tier"] = 3   # more general repairs to try next round if this one doesn't hold
                if entry["outcome"] == "NEEDS_HUMAN":
                    st["next_tier"] = 4
                    break
                if entry["outcome"] == "APPLIED":
                    st["last_applied"] = entry; applied = done_this_round = True
                    break
            if st["next_tier"] > 3 and not done_this_round:
                exhausted.append(it)
        if exhausted:
            _escalate(bid, layer, h, exhausted)
        _save(bid, layer, h)
        if not applied:
            return h["status"]
        ok = _verify(bid, layer)
        if ok is None:          # build layer: the requeued run is the check
            return h["status"]
        rec = builds.get(bid)
        if ok:
            _succeeded(bid, layer, h)
            return h["status"]
        # Some items may be fixed even though others still fail: learn those now, don't wait for all.
        still = {_sig(layer, x) for x in failures(rec, layer)}
        for sig, st in h["items"].items():
            if sig not in still:
                _learn_item(bid, layer, st)
        # The layer still fails after the fix: every learned rule that was applied this round takes a FAILED.
        # rule_lifecycle retires a rule after RETIRE_AFTER_FAILED in a row; retired rules leave learned_fixes.json
        # and the failure drops back to the LLM tier on the next round.
        _count_learned(h, "failed", {"build": bid, "layer": layer,
                                     "detail": "; ".join(it.get("key", "") for it in failures(rec, layer))[:400]})


def on_build_progress(bid: str) -> None:
    """The pipeline got past the build layer (e.g. a requeued build now reached the watcher)."""
    rec = builds.get(bid) or {}
    h = (rec.get("healing") or {}).get("build")
    if h and h.get("status") == "HEALING":
        _succeeded(bid, "build", h)


def on_failure(bid: str) -> str:
    rec = builds.get(bid) or {}
    if rec.get("refused"):
        return "REFUSED"   # v114: a refused system update is a permission answer, not something to heal
    layer = {"FAILED": "build", "NOT_QUALIFIED": "qualify", "PARTIALLY_QUALIFIED": "qualify"}.get(rec.get("state"))
    return heal(bid, layer) if layer else "NOT_FAILED"


def sweep() -> None:
    """Called from the pipeline loop: heal Coolify hand-offs that plain retrying hasn't fixed."""
    for rec in builds.all_builds():
        c = rec.get("coolify") or {}
        if rec.get("state") not in (builds.QUALIFIED, builds.PARTIALLY_QUALIFIED) or not c or c.get("status") == "DISPATCHED":
            continue
        if int(c.get("attempts", 0)) < HANDOFF_HEAL_AFTER:
            continue
        h = _state(rec, "handoff")
        open_sigs = {_sig("handoff", it) for it in failures(rec, "handoff")}
        if h["status"] == "ESCALATED" and open_sigs <= set(h["alerted"]):
            continue  # a human already has exactly these
        try:
            heal(rec["id"], "handoff")
        except Exception as e:
            print(f"maintenance handoff {rec['id']}: {e}", flush=True)
