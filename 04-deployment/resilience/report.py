"""The aggregate report of one fleet run: per-app outcome plus totals, failures by class, the concurrency
actually used, and every timeout / resource event. Built only from the per-app results and the events the
runner recorded while it ran: nothing is inferred, and an app without a result is reported NOT_STARTED."""
from __future__ import annotations
import json, os, time
from pathlib import Path

from .states import STATES, handoff_state, levels


def _row(name: str, r: dict | None, safe) -> dict:
    v = (r or {}).get("verification") or {}
    run = (r or {}).get("runner") or {}
    ev = (((r or {}).get("stages") or {}).get("6 CLEAN") or {}).get("evidence") or {}
    state = handoff_state(r)
    return {
        "app": safe(name),
        "number": (r or {}).get("number"),
        "state": state,
        "levels": levels(r),
        "verdict": (r or {}).get("verdict"),
        "broken_at": (r or {}).get("broken_at"),
        "code": (r or {}).get("code"),
        "run_id": v.get("run_id"),
        "correlation_id": v.get("correlation_id"),
        "attempt": v.get("attempt"),
        "deployment_revision": v.get("deployment_revision"),
        "app_revision": v.get("app_revision"),
        "revision_stable": v.get("revision_stable"),
        "invalidated_attempts": len(v.get("invalidated_attempts") or []),
        "evidence_dir": ev.get("dir"),
        "seconds": run.get("seconds"),
        "queued_s": ((r or {}).get("timing") or {}).get("queued_s"),
        "second_pass": bool(run.get("second_pass")),
    }


def build_report(ctx: dict, names: list[str], results: dict, safe, collisions: dict | None = None) -> dict:
    """ctx: fleet facts the runner measured (ids, times, revisions, concurrency, events).
    results: safe_id(app) -> result dict (or missing). collisions: safe_id -> [names that share it]."""
    rows, seen = [], set()
    for n in names:
        k = safe(n)
        if k in seen:
            continue
        seen.add(k)
        rows.append(_row(n, results.get(k), safe))
    for k, dup in sorted((collisions or {}).items()):
        for extra in dup[1:]:
            rows.append({"app": extra, "state": "NOT_STARTED", "code": "IDENTITY_COLLISION",
                         "levels": levels(None), "verdict": None, "broken_at": None,
                         "detail": f"'{extra}' has the same identity ({k}) as '{dup[0]}': its workspace, "
                                   "containers, ports and evidence would be shared, so it was not run. Rename one."})
    totals = {s: sum(1 for x in rows if x["state"] == s) for s in STATES}
    by_class: dict[str, dict] = {}
    for x in rows:
        if x["state"] in ("QUALIFIED",):
            continue
        key = f"{x['state']}:{x.get('broken_at') or '-'}:{x.get('code') or '-'}"
        c = by_class.setdefault(key, {"state": x["state"], "stage": x.get("broken_at"), "code": x.get("code"), "apps": []})
        c["apps"].append(x["app"])
    events = list(ctx.get("events") or [])
    fleet_stable = bool(ctx.get("revision_at_start")) and ctx.get("revision_at_start") == ctx.get("revision_at_end") \
        and ctx.get("revision_at_start") != "unknown"
    return {
        "schema": "ATTA_FLEET_RUN_REPORT.v1",
        "fleet_run_id": ctx.get("fleet_run_id"),
        "started_at": ctx.get("started_at"),
        "finished_at": ctx.get("finished_at"),
        "seconds": round((ctx.get("finished_at") or 0) - (ctx.get("started_at") or 0), 1),
        "deployment_revision": {"at_start": ctx.get("revision_at_start"), "at_end": ctx.get("revision_at_end"),
                                "stable": fleet_stable},
        "apps_requested": len(names),
        "apps_reported": len(rows),
        "totals": totals,
        "failures_by_class": sorted(by_class.values(), key=lambda c: (-len(c["apps"]), c["state"], str(c["code"]))),
        "concurrency": ctx.get("concurrency") or {},
        "timeouts": [x["app"] for x in rows if x["state"] == "TIMEOUT"],
        "resource_events": events,
        "apps": rows,
    }


def report_markdown(rep: dict) -> str:
    c = rep.get("concurrency") or {}
    t = rep["totals"]
    L = [f"# Fleet run {rep['fleet_run_id']}", "",
         f"Apps requested {rep['apps_requested']}, reported {rep['apps_reported']}. "
         + ", ".join(f"{k} {t[k]}" for k in STATES) + ".", "",
         f"Deployment revision: {rep['deployment_revision']['at_start']} -> {rep['deployment_revision']['at_end']} "
         f"({'stable' if rep['deployment_revision']['stable'] else 'CHANGED: results from the old revision are UNVERIFIED'})", "",
         f"Concurrency: mode {c.get('mode')}, cap {c.get('cap')}, peak {c.get('peak')} apps at once"
         + (f"; reason: {c.get('reason')}" if c.get("reason") else "")
         + f"; waited for machine room {c.get('gate_waits', 0)} time(s)"
         + f"; sequential second pass {len(c.get('second_pass') or [])} app(s)", ""]
    if rep["timeouts"]:
        L += ["Timeouts: " + ", ".join(rep["timeouts"]), ""]
    if rep["failures_by_class"]:
        L += ["## Not qualified, by class", "", "| State | Stage | Code | Apps |", "|---|---|---|---|"]
        L += [f"| {x['state']} | {x['stage'] or ''} | {x['code'] or ''} | {', '.join(x['apps'])} |" for x in rep["failures_by_class"]]
        L += [""]
    if rep["resource_events"]:
        L += ["## Resource and timeout events", ""]
        L += [f"- {time.strftime('%H:%M:%S', time.localtime(e.get('at') or 0))} {e.get('kind')}"
              f"{' [' + e['app'] + ']' if e.get('app') else ''}: {e.get('detail', '')}" for e in rep["resource_events"][:500]]
        L += [""]
    L += ["## Every app", "", "| App | State | TCP | HTTP | Healthy | Qualified | Verified | Stage | Code | Attempt | Run |",
          "|---|---|---|---|---|---|---|---|---|---|---|"]
    mark = {True: "yes", False: "no", None: "n/a"}
    for x in rep["apps"]:
        lv = x.get("levels") or {}
        L.append(f"| {x['app']} | {x['state']} | " + " | ".join(mark[lv.get(k)] for k in
                 ("tcp_reachable", "http_response", "healthy", "qualified", "verified"))
                 + f" | {x.get('broken_at') or ''} | {x.get('code') or ''} | {x.get('attempt') or ''} | {x.get('run_id') or ''} |")
    return "\n".join(L) + "\n"


def write_report(out_dir: Path, rep: dict) -> Path:
    """runs/<fleet_run_id>/report.{json,md} (kept) plus RUN-REPORT.{json,md} (the latest), each written
    whole-then-renamed so a reader never sees half a report."""
    out_dir = Path(out_dir)
    d = out_dir / "runs" / str(rep["fleet_run_id"])
    d.mkdir(parents=True, exist_ok=True)
    js = json.dumps(rep, indent=2, default=str) + "\n"
    md = report_markdown(rep)
    for dest, text in ((d / "report.json", js), (d / "report.md", md),
                       (out_dir / "RUN-REPORT.json", js), (out_dir / "RUN-REPORT.md", md)):
        tmp = dest.with_suffix(dest.suffix + ".tmp")
        try:
            tmp.write_text(text); os.replace(tmp, dest)
        except OSError:
            tmp.unlink(missing_ok=True)
    return d / "report.json"
