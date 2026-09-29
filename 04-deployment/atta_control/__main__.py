"""Command line for the control layer. Run from the code folder with the server's environment:

    cd /opt/atta/04-deployment          (or the folder systemd starts ATTa from)
    python3 -m atta_control <command>

  status                          gate mode, policy, control-log integrity
  after-run [--fleet ID] [--probe]
                                  everything below for one fleet run (default: the latest), in order:
                                  record observations, export failures, record diagnostic, deployments, report
  export-failures [--fleet ID]    write state/runner/failures/<fleet>/<app>/<run>-a<n>.json (never overwrites)
  diagnose [--fleet ID] [--output PATH]
                                  record diagnostic over the exported records; read-only, proven by hashes
  deployments [--probe]           per-app Coolify state; --probe makes one HTTP request to each dispatched URL
  report [--fleet ID] [--probe]   write state/runner/control/CONTROL-REPORT.{json,md}
  verify-logs                     check every control log's hash chain
  policy show                     the loaded policy, or why it cannot be trusted
  policy scaffold [--write] [--version V]
                                  propose APPROVAL_REQUIRED rules from what observe mode saw ran
  approve REMEDIATION_ID --by NAME [--ttl SECONDS] [--note TEXT]
                                  approve one remediation (single use; default expiry 24 h)

Exit status: 0 done and clean, 1 done but something needs a person (a broken log chain, a missing record,
an export conflict, an untrusted policy), 2 usage error."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import deploy, gate, policy as pol, records, report
from .store import EventStore


def _print(obj) -> None:
    print(json.dumps(obj, indent=2, sort_keys=True, default=str))


def cmd_status(a) -> int:
    store = EventStore()
    try:
        p = pol.load_policy()
        pinfo = {"path": str(pol.policy_path()), "version": p.version, "rules": len(p.rules), "trusted": True}
    except pol.PolicyError as e:
        pinfo = {"path": str(pol.policy_path()), "trusted": False, "problems": e.problems}
    integ = store.verify()
    _print({"gate_mode": gate.mode(), "mode_note": gate.mode_note(), "max_evidence_age_s": gate.max_evidence_age(),
            "policy": pinfo, "control_root": str(store.root),
            "logs": {Path(k).name: {"events": v["events"], "ok": v["ok"]} for k, v in integ["logs"].items()}})
    return 0 if integ["ok"] and pinfo["trusted"] else 1


def cmd_export(a) -> int:
    out = records.export_failures(a.fleet)
    _print(out)
    return 0 if out.get("ok") else 1


def cmd_diagnose(a) -> int:
    out = records.diagnose(a.fleet, out_path=Path(a.output) if a.output else None)
    _print({k: v for k, v in out.items() if k != "findings"} | {"findings": len(out.get("findings") or [])})
    return 0 if out.get("ok") else 1


def cmd_deployments(a) -> int:
    out = deploy.verify(probe=a.probe)
    _print(out)
    return 0


def cmd_report(a) -> int:
    diag = None
    fid = a.fleet or (records.fleet_report() or {}).get("fleet_run_id")
    if fid and (records.failures_root() / records.segment(fid)).is_dir():
        diag = records.diagnose(fid)
    rep = report.build(a.fleet, diagnostic=diag, deployments=deploy.verify(probe=a.probe))
    where = report.write(rep)
    print(report.markdown(rep))
    print(f"written: {where}")
    return 0 if rep["control_logs"]["ok"] else 1


def cmd_after_run(a) -> int:
    fleet = records.fleet_report(a.fleet)
    if not fleet:
        print(f"no fleet report for {a.fleet or 'the latest run'} under {records.runner_dir()}", file=sys.stderr)
        return 1
    fid = fleet["fleet_run_id"]
    store = EventStore()
    obs = records.record_observations(fleet, store)
    exp = records.export_failures(fid, store=store)
    out_dir = records.runner_dir() / "control" / "runs" / records.segment(fid)
    diag = records.diagnose(fid, store=store, out_path=out_dir / "record-diagnostic.json")
    dep = deploy.verify(probe=a.probe, store=store)
    rep = report.build(fid, store=store, diagnostic=diag, deployments=dep)
    where = report.write(rep, store=store)
    _print({"fleet_run_id": fid, "observations": obs, "export": exp["summary"],
            "diagnostic": {k: diag.get(k) for k in ("processed_count", "expected_count", "missing_apps", "inputs_unchanged")},
            "deployments": dep["totals"], "control_logs_ok": rep["control_logs"]["ok"], "report": str(where)})
    clean = exp.get("ok") and diag.get("ok") and rep["control_logs"]["ok"]
    return 0 if clean else 1


def cmd_verify_logs(a) -> int:
    out = EventStore().verify()
    _print(out)
    return 0 if out["ok"] else 1


def cmd_policy(a) -> int:
    if a.what == "show":
        try:
            p = pol.load_policy()
        except pol.PolicyError as e:
            _print({"path": str(pol.policy_path()), "trusted": False, "problems": e.problems})
            return 1
        _print({"path": str(pol.policy_path()), "source": p.source, **p.to_dict()})
        return 0
    try:
        existing = pol.load_policy()
    except pol.PolicyError as e:
        print(f"the current policy cannot be trusted, so it will not be extended: {e}", file=sys.stderr)
        return 1
    store = EventStore()
    proposed = pol.scaffold(store.events(store.AUDIT), version=a.version or f"scaffold-{int(__import__('time').time())}",
                            existing=existing)
    if a.write:
        where = pol.save_policy(proposed, pol.policy_path())
        print(f"written: {where} ({len(proposed.rules)} rule(s); new ones are APPROVAL_REQUIRED)")
    else:
        _print(proposed.to_dict())
        print("(not written: add --write to save it as the policy)", file=sys.stderr)
    return 0


def cmd_approve(a) -> int:
    try:
        e = gate.approve(EventStore(), a.remediation_id, a.by, ttl=a.ttl, note=a.note or "")
    except ValueError as err:
        print(str(err), file=sys.stderr)
        return 2
    c = e["covers"]
    print(f"approved {e['remediation_id']} for one use, until "
          f"{__import__('time').strftime('%Y-%m-%d %H:%M:%SZ', __import__('time').gmtime(e['expires_at']))}:\n"
          f"  {c.get('fix_name')} {json.dumps(c.get('args'), sort_keys=True)}\n"
          f"  for {c.get('failure_key')} on {c.get('app') or 'build ' + str(c.get('build_id'))}")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python3 -m atta_control", description=__doc__.split("\n\n")[0],
                                 formatter_class=argparse.RawDescriptionHelpFormatter, epilog=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status").set_defaults(fn=cmd_status)
    p = sub.add_parser("after-run"); p.add_argument("--fleet"); p.add_argument("--probe", action="store_true")
    p.set_defaults(fn=cmd_after_run)
    p = sub.add_parser("export-failures"); p.add_argument("--fleet"); p.set_defaults(fn=cmd_export)
    p = sub.add_parser("diagnose"); p.add_argument("--fleet"); p.add_argument("--output"); p.set_defaults(fn=cmd_diagnose)
    p = sub.add_parser("deployments"); p.add_argument("--probe", action="store_true"); p.set_defaults(fn=cmd_deployments)
    p = sub.add_parser("report"); p.add_argument("--fleet"); p.add_argument("--probe", action="store_true")
    p.set_defaults(fn=cmd_report)
    sub.add_parser("verify-logs").set_defaults(fn=cmd_verify_logs)
    p = sub.add_parser("policy"); p.add_argument("what", choices=("show", "scaffold"))
    p.add_argument("--write", action="store_true"); p.add_argument("--version"); p.set_defaults(fn=cmd_policy)
    p = sub.add_parser("approve"); p.add_argument("remediation_id"); p.add_argument("--by", required=True)
    p.add_argument("--ttl", type=int); p.add_argument("--note"); p.set_defaults(fn=cmd_approve)
    a = ap.parse_args(argv)
    return a.fn(a)


if __name__ == "__main__":
    raise SystemExit(main())
