"""TEST FIXTURE (S01B-T41/T42): starts a DETACHED descendant that would outlive its parent, records its pid in the
working directory, then blocks until the runtime terminates the process tree. Harmless (only sleeps)."""
import os
import subprocess
import sys
import time


def healthcheck():
    if os.name == "nt":
        flags = subprocess.CREATE_NEW_PROCESS_GROUP | 0x00000008   # DETACHED_PROCESS
        p = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"], creationflags=flags)
        pid = p.pid
    else:
        r, w = os.pipe()
        if os.fork() == 0:
            os.setsid()
            if os.fork() == 0:
                os.write(w, str(os.getpid()).encode())
                time.sleep(120)
                os._exit(0)
            os._exit(0)
        pid = int(os.read(r, 32).decode())
    with open("descendant.pid", "w") as f:
        f.write(str(pid))
    time.sleep(60)
    return {"healthy": True}


def noop(payload, ctx):
    return {"ok": True}


def verify_any(payload, output):
    return True
