#!/usr/bin/env python3
"""deploystate — how bootstrap.sh writes to the deployment record (v117). Same state machine as ADM (adm/deployment.py).

  deploystate.py begin                       ADM job (ATTA_DEPLOYMENT_ID set, record exists): prints its id.
                                             Hand-typed `bash run`: opens a `manual` record, prints the new id.
  deploystate.py to STATE [REASON]           move the record (refused if the state machine doesn't allow it)
  deploystate.py set KEY JSON                record a fact (attempted, transaction, local_restore, ...)
  deploystate.py rollback RESULT [REASON]    record the rollback outcome (succeeded|failed|not_needed|blocked)
  deploystate.py state                       print the current state
The record id comes from ATTA_DEPLOYMENT_ID. Exit 0 = done; 1 = refused (message on stderr); 2 = usage.
"""
import json, os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from adm import deployment, journal  # noqa: E402


def main(argv):
    if not argv:
        print(__doc__, file=sys.stderr); return 2
    cmd, a = argv[0], argv[1:]
    job = os.environ.get("ATTA_DEPLOYMENT_ID", "")
    try:
        if cmd == "begin":
            if job:
                if deployment.get(job) is None or "state" not in deployment.get(job):
                    print(f"deploystate: no deployment record {job!r} to continue", file=sys.stderr); return 1
                print(job); return 0
            job = journal.new_job_id() + "-manual"
            deployment.create(job, kind="manual", origin="local", requested_by=os.environ.get("SUDO_USER") or "root")
            deployment.transition(job, "validating")
            print(job); return 0
        if not job:
            print("deploystate: ATTA_DEPLOYMENT_ID is not set", file=sys.stderr); return 2
        if cmd == "to" and 1 <= len(a) <= 2:
            deployment.transition(job, a[0], reason=a[1] if len(a) == 2 else None); return 0
        if cmd == "set" and len(a) == 2:
            deployment.set_fields(job, **{a[0]: json.loads(a[1])}); return 0
        if cmd == "rollback" and 1 <= len(a) <= 2:
            deployment.set_rollback(job, a[0], **({"reason": a[1]} if len(a) == 2 else {})); return 0
        if cmd == "state" and not a:
            d = deployment.get(job)
            print((d or {}).get("state", "")); return 0 if d else 1
    except deployment.IllegalTransition as e:
        print(f"deploystate: {e}", file=sys.stderr); return 1
    except (ValueError, OSError) as e:
        print(f"deploystate: {e}", file=sys.stderr); return 1
    print(__doc__, file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
