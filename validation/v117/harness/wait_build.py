#!/usr/bin/env python3
"""wait_build.py USER BUILD_ID [TIMEOUT_S] — poll GET /api/builds/<id> until a terminal state; print a summary."""
import json, os, subprocess, sys, time
TERMINAL = {"QUALIFIED", "PARTIALLY_QUALIFIED", "NOT_QUALIFIED", "FAILED", "PACKAGE_INSTALLED", "REFUSED"}
user, bid = sys.argv[1], sys.argv[2]; deadline = time.time() + float(sys.argv[3] if len(sys.argv) > 3 else 3600)
last = None
while True:
    d = json.loads(subprocess.run(["python3", os.path.expanduser("~/atta_client.py"), "build", user, bid], capture_output=True, text=True).stdout or "{}")
    s = d.get("state")
    if s != last: print(time.strftime("%H:%M:%S"), s, flush=True); last = s
    if s in TERMINAL or time.time() > deadline: break
    time.sleep(5)
print(json.dumps({k: d.get(k) for k in ("id", "owner", "state", "error", "apps_added", "apps_already_in_library", "qualification", "coolify")}, indent=1, default=str)[:6000])
