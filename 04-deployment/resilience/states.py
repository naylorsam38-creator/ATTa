"""The per-app outcome vocabulary of a fleet run, and the observation ladder behind it.

Outcome (exactly one per app, never inferred beyond the evidence on the result):
  NOT_STARTED   no job ran for the app (no result, identity collision, never dequeued)
  START_FAILED  the runner could not bring the app up (no way of running it worked)
  TIMEOUT       the app, or the worker checking it, ran out of time
  INCONCLUSIVE  the check itself could not reach a verdict (runner/watcher/probe exception, no Docker,
                no browser); says nothing about the app
  FAILED        the app ran and a qualification stage failed
  QUALIFIED     every stage passed AND the final verification barrier held (same revision before/after)
  UNVERIFIED    the checks ran but the deployment or the app changed underneath them; must be re-run

Observation ladder (each rung recorded separately; a higher rung never implies itself from a lower one):
  tcp_reachable -> http_response -> healthy -> qualified -> verified
"""
from __future__ import annotations

STATES = ("NOT_STARTED", "START_FAILED", "TIMEOUT", "INCONCLUSIVE", "FAILED", "QUALIFIED", "UNVERIFIED")

# The check could not decide: nothing here is evidence about the app itself.
INCONCLUSIVE_CODES = {"RUNNER_EXCEPTION", "WORKER_EXCEPTION", "WATCHER_EXCEPTION", "SMOKE_EXCEPTION",
                      "BROWSER_EXCEPTION", "PLAYWRIGHT_UNAVAILABLE", "RUNNER_NO_DOCKER", "IDENTITY_COLLISION"}
TIMEOUT_CODES = {"TIMEOUT", "WORKER_TIMEOUT", "PROBE_TIMEOUT"}
# Runner rules that mean "waited as long as allowed and it never came up".
TIMEOUT_RULES = {"budget.spent", "app.still_starting", "app.stalled"}
START_STAGES = {"1 INSTALLED", "2 APP_UP"}


def _stage_ok(r: dict, name: str) -> bool:
    return ((r.get("stages") or {}).get(name) or {}).get("status") == "OK"


def passed_all_stages(r: dict | None) -> bool:
    """Every stage passed, stage 6 included (what v121 called PASS). Not yet QUALIFIED: the barrier decides."""
    return bool(r) and r.get("broken_at") is None and _stage_ok(r, "6 CLEAN")


def _ran_out_of_time(r: dict) -> bool:
    run = r.get("runner") or {}
    attempts = run.get("attempts") or []
    if r.get("code") != "RUNNER_EXHAUSTED" or not attempts:
        return False
    return any(a.get("rule") == "budget.spent" for a in attempts) or attempts[-1].get("rule") in TIMEOUT_RULES


def handoff_state(r: dict | None) -> str:
    if not r:
        return "NOT_STARTED"
    if r.get("handoff_state") == "NOT_STARTED":
        return "NOT_STARTED"
    v = r.get("verification") or {}
    if r.get("verdict") == "UNVERIFIED" or v.get("status") == "UNVERIFIED":
        return "UNVERIFIED"
    code = str(r.get("code") or "")
    if r.get("broken_at") is None:
        # Nothing failed, but QUALIFIED needs stage 6 OK and a barrier that held. Anything less is not a pass.
        return "QUALIFIED" if passed_all_stages(r) and v.get("status") == "CURRENT" else "INCONCLUSIVE"
    if code in INCONCLUSIVE_CODES or code.startswith("PROBE_"):
        return "INCONCLUSIVE"
    if code in TIMEOUT_CODES or _ran_out_of_time(r):
        return "TIMEOUT"
    if r.get("broken_at") in START_STAGES and not (r.get("runner") or {}).get("started"):
        return "START_FAILED"
    return "FAILED"


def levels(r: dict | None) -> dict:
    """What was actually observed for the app, rung by rung. None = not applicable (a system project or
    package is never started, so it has no port to reach), False = checked and not seen / not checked."""
    if not r:
        return {"tcp_reachable": False, "http_response": False, "healthy": False, "qualified": False, "verified": False}
    run = r.get("runner") or {}
    probe = (r.get("observations") or {}).get("app_http") or {}
    v = r.get("verification") or {}
    qualified = bool(v.get("passed_checks")) if "passed_checks" in v else passed_all_stages(r)
    verified = qualified and v.get("status") == "CURRENT" and handoff_state(r) == "QUALIFIED"
    if run.get("profile") in ("system", "package"):
        return {"tcp_reachable": None, "http_response": None, "healthy": None, "qualified": qualified, "verified": verified}
    started = bool(run.get("started"))
    return {
        "tcp_reachable": bool(probe.get("tcp_connected")) if probe else False,
        "http_response": probe.get("http_status") is not None if probe else False,
        # healthy: the runner saw it settle (no 5xx for SETTLE_SECONDS) and the watcher reached app and proxy
        "healthy": started and _stage_ok(r, "2 APP_UP") and _stage_ok(r, "3 PROXY_UP"),
        "qualified": qualified,
        "verified": verified,
    }
