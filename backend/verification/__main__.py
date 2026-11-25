"""python -m backend.verification {plan,begin,verify-task,verify-stage,status,seal,integrity,checkpoints,recover}"""
from __future__ import annotations

import argparse
import getpass
import json
import sys
from pathlib import Path

from backend.settings import REPO_ROOT

from .build_controller import BuildController, ControllerError, recover


def _applied_migrations() -> list[str] | None:
    try:
        from backend.database.migrate import status
        from backend.settings import load_settings
        return [s["name"].split(":", 1)[1] for s in status(load_settings()) if s["applied"]
                and s["name"].startswith("migration:")]
    except Exception:  # noqa: BLE001 — database unreachable: compatibility judged on code only
        return None


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="backend.verification")
    ap.add_argument("--root", type=Path, default=REPO_ROOT)
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("plan", "status"):
        p = sub.add_parser(name)
        p.add_argument("stage")
    p = sub.add_parser("begin")
    p.add_argument("stage")
    p.add_argument("task")
    p = sub.add_parser("verify-task")
    p.add_argument("stage")
    p.add_argument("task")
    p.add_argument("--base")
    p = sub.add_parser("verify-stage")
    p.add_argument("stage")
    p.add_argument("--base")
    sub.add_parser("seal").add_argument("--operator", default=getpass.getuser())
    sub.add_parser("integrity")
    sub.add_parser("checkpoints")
    p = sub.add_parser("recover")
    p.add_argument("--checkpoint")
    p.add_argument("--yes", action="store_true")
    a = ap.parse_args(argv)
    try:
        bc = BuildController(a.root)
        if a.cmd in ("plan", "status"):
            for row in bc.plan(a.stage):
                print(f"{row['task']:<14} {row['status']:<12} deps={','.join(row['depends_on']) or '-':<30} "
                      f"tests={','.join(row['tests'])}")
            print(f"stage gate {a.stage}: {'PASSED' if bc.stage_gate_passed(a.stage) else 'NOT PASSED'}")
            return 0
        if a.cmd == "begin":
            print(json.dumps(bc.begin_task(a.stage, a.task), indent=2))
            return 0
        if a.cmd == "verify-task":
            o = bc.verify_task(a.stage, a.task, a.base)
            print(json.dumps(o.__dict__, indent=2))
            return 0 if o.status == "PASSED" else 1
        if a.cmd == "verify-stage":
            r = bc.verify_stage(a.stage, a.base)
            for t in r["tasks"]:
                print(f"{t['task_id']:<14} {t['status']}")
                for test in t["tests"]:
                    print(f"    {test['test']:<10} {'PASS' if test['passed'] else 'FAIL'} exit={test['exit_code']} "
                          f"{test['duration_s']}s  {test['evidence']}")
                for reason in t["reasons"]:
                    print(f"    ! {reason}")
            for fp in r["file_problems"]:
                print(f"  ! {fp}")
            print(f"STAGE {a.stage} GATE: {'PASSED' if r['passed'] else 'FAILED'}  run={r['run']}")
            if r["checkpoint"]:
                print(f"checkpoint: {json.dumps(r['checkpoint'])}")
            return 0 if r["passed"] else 1
        if a.cmd == "seal":
            lock = bc.seal(a.operator)
            print(f"sealed {len(lock['files'])} protected files (signed={lock['signed']})")
            return 0
        if a.cmd == "integrity":
            problems = bc.check_integrity() + bc.verify_ledger()
            print("INTACT" if not problems else "\n".join(problems))
            return 0 if not problems else 1
        if a.cmd == "checkpoints":
            print(json.dumps(bc.checkpoints(), indent=2))
            return 0
        if a.cmd == "recover":
            print(json.dumps(recover(bc, a.checkpoint, _applied_migrations(), a.yes), indent=2))
            return 0
    except ControllerError as exc:
        print(f"BUILD CONTROLLER REFUSED: {exc}", file=sys.stderr)
        return 2
    return 1


if __name__ == "__main__":
    sys.exit(main())
