"""TEST FIXTURE (S01B-T41, Windows): tries to escape the worker restrictions and reports what happened. Harmless:
reads/writes a test-only secret file, tries to start processes, tries one outbound TCP connection."""
import json
import os
import shutil
import socket
import subprocess
import sys


def _try(fn):
    try:
        return "ALLOWED: " + str(fn())[:80]
    except Exception as exc:  # noqa: BLE001
        return f"denied: {type(exc).__name__}: {exc}"[:200]


def healthcheck():
    t = json.load(open("probe_targets.json"))
    out = {"pid": os.getpid()}
    out["secret_read"] = _try(lambda: open(t["secret"]).read())
    out["secret_write"] = _try(lambda: open(t["secret"], "a").write("x"))
    out["spawn_cmd"] = _try(lambda: subprocess.run(["cmd.exe", "/c", "echo", "spawned"], capture_output=True,
                                                   text=True, timeout=10).stdout)
    def copy_and_run():
        dst = os.path.abspath("renamed-interpreter.exe")
        shutil.copy2(sys.executable, dst)
        return subprocess.run([dst, "-c", "print('ran')"], capture_output=True, text=True, timeout=10).stdout
    out["spawn_renamed_interpreter"] = _try(copy_and_run)
    out["connect_external"] = _try(lambda: socket.create_connection(("1.1.1.1", 443), timeout=3).close())
    return {"healthy": True, "probe": out}


def noop(payload, ctx):
    return {"ok": True}


def verify_any(payload, output):
    return True
