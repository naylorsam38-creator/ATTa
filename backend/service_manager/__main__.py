"""CLI: python -m backend.service_manager {run,start,stop,status,healthcheck,validate,order}"""
from __future__ import annotations

import argparse
import json
import sys

from backend.contracts import configure_logging
from backend.settings import ConfigurationError, load_settings
from backend.settings.loader import resolve_path

from . import control
from .manager import ServiceManager


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="backend.service_manager")
    ap.add_argument("command", choices=["run", "start", "stop", "status", "healthcheck", "validate", "order"])
    ap.add_argument("--env", default=None, help="environment (default: ATTA_ENV or development)")
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    ap.add_argument("--timeout", type=float, default=120.0)
    args = ap.parse_args(argv)
    try:
        settings = load_settings(args.env)
    except ConfigurationError as exc:
        print(f"CONFIGURATION INVALID\n{exc}", file=sys.stderr)
        return 4
    log_dir = resolve_path(settings.logging.directory)
    log_dir.mkdir(parents=True, exist_ok=True)
    configure_logging(settings.logging.level, settings.logging.format,
                      str(log_dir / "manager.jsonl") if args.command == "run" else None)
    out = (lambda o: print(json.dumps(o, indent=2, default=str))) if args.json else None

    if args.command == "validate":
        mgr = ServiceManager(settings)
        problems = mgr.validate_dependencies()
        for w in settings.warnings():
            print(f"WARNING: {w}")
        print("CONFIGURATION VALID" if not problems else "DEPENDENCY PROBLEMS:\n  - " + "\n  - ".join(problems))
        return 0 if not problems else 5
    if args.command == "order":
        print(" -> ".join(ServiceManager(settings).startup_order()))
        return 0
    if args.command == "run":
        return ServiceManager(settings).run_forever()
    if args.command == "start":
        code, st = control.start_detached(settings, timeout=args.timeout)
        if out:
            out(st)
        else:
            _print_state(st)
            print("READY" if code == 0 else "NOT READY")
        return code
    if args.command == "stop":
        code, msg = control.stop(settings, timeout=args.timeout)
        print(msg)
        return code
    if args.command == "status":
        mgr = ServiceManager(settings)
        st = control.read_state(mgr)
        running = control.manager_pid(mgr) is not None
        if out:
            out({"running": running, **(st or {})})
        else:
            print(f"manager running: {running}")
            _print_state(st)
        return 0 if running and st and st.get("ready") else 1
    if args.command == "healthcheck":
        code, report = control.healthcheck(settings)
        if out:
            out(report)
        else:
            for c in report["checks"]:
                mark = "PASS" if c["ok"] else ("FAIL" if c["essential"] else "WARN")
                print(f"[{mark}] {c['check']}: {c['detail']}")
            print("HEALTHY" if code == 0 else "UNHEALTHY")
        return code
    return 1


def _print_state(st: dict | None) -> None:
    if not st:
        print("no state recorded")
        return
    if "error" in st:
        print(st["error"])
    print(f"state: {st.get('state')}  ready: {st.get('ready')}")
    for r in st.get("reasons", []):
        print(f"  reason: {r}")
    for n, s in (st.get("services") or {}).items():
        print(f"  service {n:<15} {s['status']:<11} essential={s['essential']} pid={s['pid']} {s['detail'][:100]}")
    for c in st.get("capabilities", []):
        print(f"  capability {c['id']:<30} {c['state']:<20} {'; '.join(c['problems'])[:100]}")


if __name__ == "__main__":
    sys.exit(main())
