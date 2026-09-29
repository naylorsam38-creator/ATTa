"""The control report of one fleet run: every app's outcome, attempts, stages reached, failure stage, evidence,
the repairs the gate saw for it, its record-diagnostic finding and its Coolify deployment state; then the
run's execution facts and the integrity of the control logs.

Built only from what is on disk (the fleet report, results/, the exported failure records, the build records
and the control logs). A fact the records do not hold is written as UNKNOWN, never estimated.

Written to <ROOT>/state/runner/control/runs/<fleet run id>/control-report.{json,md} (kept) and
<ROOT>/state/runner/control/CONTROL-REPORT.{json,md} (the latest). The fleet's own RUN-REPORT.* is never
touched: it stays the runner's."""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

from . import deploy, gate, records
from .store import EventStore

SCHEMA = "ATTA_CONTROL_REPORT.v1"
UNKNOWN = "UNKNOWN"


def _k(x: Any) -> Any:
    return UNKNOWN if x is None else x


def _repairs_for(app: str, run_id: str | None, audit: list[dict]) -> dict:
    """The gate's record for this app's job: decisions and outcomes, counted, plus the remediation ids."""
    def names(e: dict) -> set[str]:
        return {records.safe_id(x) for x in ((e.get("identity") or {}).get("app"), (e.get("evidence") or {}).get("app")) if x}
    mine = [e for e in audit if app in names(e)]
    if run_id:   # this fleet's job only: repairs made on an earlier run of the same app are not this run's
        mine = [e for e in mine if (e.get("identity") or {}).get("run_id") in (run_id, None)]
    auth = [e for e in mine if e.get("event") == "REPAIR_AUTHORIZATION"]
    return {
        "decisions": len(auth),
        "would_block": sum(1 for e in auth if e.get("would_block")),
        "started": sum(1 for e in mine if e.get("event") == "REPAIR_STARTED"),
        "returned": sum(1 for e in mine if e.get("event") == "REPAIR_RETURNED"),
        "raised": sum(1 for e in mine if e.get("event") == "REPAIR_EXCEPTION"),
        "actions": sorted({f"{e.get('fix_name')} ({e.get('decision')})" for e in auth}),
        "remediation_ids": sorted({e.get("remediation_id") for e in auth if e.get("remediation_id")}),
    }


def build(fleet_run_id: str | None = None, base: Path | None = None, store: EventStore | None = None,
          diagnostic: dict | None = None, deployments: dict | None = None) -> dict:
    fleet = records.fleet_report(fleet_run_id, base)
    if not fleet:
        raise FileNotFoundError(f"no fleet report for {fleet_run_id or 'the latest run'} under {records.runner_dir(base)}")
    store = store or EventStore()
    results = records.load_results(base)
    audit = store.events(store.AUDIT)
    dep = (deployments or deploy.verify(probe=False, base=base, store=store))
    dep_by_app = {r["app"]: r for r in dep.get("apps") or []}
    diag_by_app = {f.get("app"): f for f in (diagnostic or {}).get("findings") or [] if f.get("app")}
    exported = {records.safe_id(r.get("app") or ""): str(p) for p, r in records.iter_exported(fleet["fleet_run_id"], base)}

    apps = []
    for row in fleet.get("apps") or []:
        app = records.safe_id(row.get("app") or "")
        got = results.get(app)
        r, why = None, "no result on disk"
        if row.get("code") == "IDENTITY_COLLISION":
            app, got, why = row.get("app") or app, None, "not run: its name maps to the same id as another app"
        if got is not None:
            ok, why = records.belongs_to_fleet(got[1], fleet)
            r = got[1] if ok else None
        v = (r or {}).get("verification") or {}
        invalid = v.get("invalidated_attempts") or []
        d = diag_by_app.get(app) or {}
        apps.append({
            "app": app,
            "final_state": _k(row.get("state")),
            "attempt_count": v.get("attempt") if isinstance(v.get("attempt"), int) else UNKNOWN,
            "invalidated_attempts": len(invalid) if r else _k(row.get("invalidated_attempts")),
            "run_id": _k(row.get("run_id")),
            "stages_reached": records.stages_reached(r) if r else UNKNOWN,
            "failure_stage": row.get("broken_at"),
            "code": row.get("code"),
            "evidence_location": row.get("evidence_dir") or None,
            "failure_record": exported.get(app),
            "result_source": why if r is None else "this fleet's result",
            "repairs": _repairs_for(app, row.get("run_id"), audit),
            "diagnostic": {"classification": d.get("classification"), "allowed_action": d.get("allowed_action"),
                           "root_cause": (d.get("root_cause") or {}).get("value")} if d else None,
            "deployment": {k: (dep_by_app.get(app) or {}).get(k) for k in ("state", "build_id", "url", "why")}
                          if app in dep_by_app else {"state": "NOT_REQUESTED", "why": "no hand-off recorded for this app"},
        })

    events = fleet.get("resource_events") or []
    conc = fleet.get("concurrency") or {}
    worker_exc = sorted({a["app"] for a in apps if a.get("code") == "WORKER_EXCEPTION"} |
                        {e.get("app") for e in events if e.get("kind") == "WORKER_EXCEPTION" and e.get("app")})
    integrity = store.verify()
    return {
        "schema": SCHEMA,
        "generated_at": time.time(),
        "fleet_run_id": fleet.get("fleet_run_id"),
        "started_at": fleet.get("started_at"),
        "finished_at": fleet.get("finished_at"),
        "deployment_revision": fleet.get("deployment_revision"),
        "gate_mode": gate.mode(),
        "apps_requested": fleet.get("apps_requested"),
        "totals": fleet.get("totals"),
        "execution": {
            "mode": _k(conc.get("mode")),
            "configured_cap": ("unset (all apps at once)" if conc.get("configured") == 0 else _k(conc.get("configured"))),
            "effective_cap": _k(conc.get("cap")),
            "peak_concurrency": _k(conc.get("peak")),
            "waits_for_machine_room": _k(conc.get("gate_waits")),
            "disk_prunes": sum(1 for e in events if e.get("kind") == "DISK_LOW_PRUNE"),
            "worker_exceptions": worker_exc,
            "timeouts": fleet.get("timeouts") or [],
            "late_results": records.late_results(fleet, base),
            "sequential_second_pass": conc.get("second_pass") or [],
            "hard_timeout_s": _k(conc.get("hard_timeout_s")),
        },
        "diagnostic": {k: (diagnostic or {}).get(k) for k in ("processed_count", "expected_count", "missing_apps",
                                                                 "inputs_unchanged")} if diagnostic else None,
        "deployments": dep.get("totals"),
        "control_logs": {"ok": integrity["ok"],
                         "logs": {Path(k).name: {"events": v["events"], "ok": v["ok"], "head": v.get("head"),
                                                 "problems": v["problems"][:20]}
                                  for k, v in integrity["logs"].items()}},
        "apps": apps,
    }


def markdown(rep: dict) -> str:
    ex = rep["execution"]
    t = rep.get("totals") or {}
    dr = rep.get("deployment_revision") or {}
    L = [f"# Control report: fleet {rep['fleet_run_id']}", "",
         f"Generated {time.strftime('%Y-%m-%d %H:%M:%SZ', time.gmtime(rep['generated_at']))} from the records on disk. "
         f"Repair gate mode: **{rep['gate_mode']}**.", "",
         "Outcomes: " + (", ".join(f"{k} {v}" for k, v in t.items()) or UNKNOWN) + ".", "",
         f"Deployment revision: {dr.get('at_start', UNKNOWN)} -> {dr.get('at_end', UNKNOWN)} "
         f"({'stable' if dr.get('stable') else 'CHANGED or UNKNOWN'}).", "",
         "## Execution", "",
         f"- Mode: `{ex['mode']}`; configured cap: `{ex['configured_cap']}`; effective cap: `{ex['effective_cap']}`; "
         f"peak concurrency: `{ex['peak_concurrency']}`",
         f"- Waits for machine room: `{ex['waits_for_machine_room']}`; disk prunes: `{ex['disk_prunes']}`",
         f"- Worker exceptions: {', '.join(ex['worker_exceptions']) or 'none'}",
         f"- Timeouts: {', '.join(ex['timeouts']) or 'none'}",
         f"- Late results (kept aside, never over the recorded one): {', '.join(ex['late_results']) or 'none'}",
         f"- Sequential second pass: {', '.join(ex['sequential_second_pass']) or 'none'}", ""]
    if rep.get("diagnostic"):
        d = rep["diagnostic"]
        L += ["## Record diagnostic", "",
              f"Processed {d.get('processed_count')} of {d.get('expected_count')} failure records; "
              f"missing: {', '.join(d.get('missing_apps') or []) or 'none'}; "
              f"inputs unchanged: {d.get('inputs_unchanged')}.", ""]
    L += ["## Applications", "",
          "| App | State | Attempts | Stages reached | Failure stage | Code | Diagnostic | Repairs (would block) | Coolify | Evidence |",
          "|---|---|---:|---|---|---|---|---|---|---|"]
    for a in rep["apps"]:
        st = a["stages_reached"]
        stages = UNKNOWN if st == UNKNOWN else (", ".join(s.split(" ", 1)[0] for s in st) or "none")
        diag = a["diagnostic"] or {}
        diag_s = f"{diag.get('classification')} / {diag.get('allowed_action')}" if diag else "—"
        rp = a["repairs"]
        L.append(f"| {a['app']} | {a['final_state']} | {a['attempt_count']} | {stages} | {a['failure_stage'] or '—'} | "
                 f"{a['code'] or '—'} | {diag_s} | {rp['decisions']} ({rp['would_block']}) | "
                 f"{(a['deployment'] or {}).get('state') or UNKNOWN} | {a['evidence_location'] or '—'} |")
    cl = rep["control_logs"]
    L += ["", "## Control log integrity", "",
          f"All hash chains intact: **{'yes' if cl['ok'] else 'NO'}**.", ""]
    for name, v in cl["logs"].items():
        L.append(f"- `{name}`: {v['events']} event(s), {'ok' if v['ok'] else 'BROKEN: ' + '; '.join(v['problems'])}"
                 + (f"; head `{v['head']}`" if v.get("head") else ""))
    L += ["", "Keep a copy of these head hashes off this machine: with them, a later rewrite of a whole log is evident too."]
    L += ["", "> Built only from persisted ATTa records. UNKNOWN means the records do not hold that fact."]
    return "\n".join(L) + "\n"


def _atomic(dest: Path, text: str) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, dest)


def write(rep: dict, base: Path | None = None, store: EventStore | None = None) -> Path:
    out = records.runner_dir(base) / "control"
    run_dir = out / "runs" / records.segment(rep["fleet_run_id"])
    js = json.dumps(rep, indent=2, sort_keys=True, default=str) + "\n"
    md = markdown(rep)
    for dest, text in ((run_dir / "control-report.json", js), (run_dir / "control-report.md", md),
                       (out / "CONTROL-REPORT.json", js), (out / "CONTROL-REPORT.md", md)):
        _atomic(dest, text)
    try:
        (store or EventStore()).append_run_event({"event": "CONTROL_REPORT_WRITTEN", "fleet_run_id": rep["fleet_run_id"],
                                                  "path": str(run_dir / "control-report.json")})
    except Exception as e:
        print(f"atta_control report: run event not recorded ({type(e).__name__}: {e})", flush=True)
    return run_dir / "control-report.json"
