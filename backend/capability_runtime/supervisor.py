"""Process supervisor for one isolated handler run. Started with `python -I` in its own session.

  * Linux: PR_SET_CHILD_SUBREAPER, so orphaned/daemonised descendants re-parent to this process.
  * Kills EVERY descendant when the handler returns, on deadline, on SIGTERM (cancellation) and when the parent
    (controller/API) disappears. Reports how many leftover processes it had to kill."""
import ctypes
import json
import os
import signal
import subprocess
import sys
import threading
import time

import psutil

CHILD = os.path.join(os.path.dirname(os.path.abspath(__file__)), "child.py")
_done = threading.Event()


def make_subreaper():
    if sys.platform.startswith("linux"):
        try:
            libc = ctypes.CDLL(None, use_errno=True)
            return libc.prctl(36, 1, 0, 0, 0) == 0  # PR_SET_CHILD_SUBREAPER
        except Exception:  # noqa: BLE001
            return False
    return False


def kill_descendants():
    """SIGKILL every descendant until none remain (handles processes forking while we kill)."""
    killed = 0
    me = psutil.Process()
    for _ in range(100):
        kids = me.children(recursive=True)
        if not kids:
            break
        for k in kids:
            try:
                k.kill()
                killed += 1
            except psutil.Error:
                pass
        while True:  # reap zombies re-parented to us
            try:
                pid, _ = os.waitpid(-1, os.WNOHANG)
            except ChildProcessError:
                break
            if pid == 0:
                break
        time.sleep(0.02)
    return killed


CGROUP_V2 = "/sys/fs/cgroup"
CGROUP_V1 = {"memory": "/sys/fs/cgroup/memory", "pids": "/sys/fs/cgroup/pids"}


def make_cgroups(req):
    """Per-execution cgroups (v2 unified, else v1 memory+pids) when the host allows it. Returns (procs files, dirs)."""
    iso = req.get("isolation") or {}
    mem = int(iso.get("memory_mb") or req.get("memory_limit_mb") or 1024) * 1024 * 1024
    pids = int(iso.get("pids_max") or 256)
    name = "atta-" + str(os.getpid())
    procs, dirs = [], []
    try:
        if os.path.isfile(os.path.join(CGROUP_V2, "cgroup.controllers")):
            d = os.path.join(CGROUP_V2, name)
            os.mkdir(d)
            dirs.append(d)
            for f, v in (("memory.max", mem), ("pids.max", pids)):
                try:
                    with open(os.path.join(d, f), "w") as fh:
                        fh.write(str(v))
                except OSError:
                    pass
            procs.append(os.path.join(d, "cgroup.procs"))
        else:
            for ctrl, base in CGROUP_V1.items():
                if os.access(base, os.W_OK):
                    d = os.path.join(base, name)
                    os.mkdir(d)
                    dirs.append(d)
                    f, v = ("memory.limit_in_bytes", mem) if ctrl == "memory" else ("pids.max", pids)
                    with open(os.path.join(d, f), "w") as fh:
                        fh.write(str(v))
                    procs.append(os.path.join(d, "cgroup.procs"))
    except OSError:
        pass
    return procs, dirs


def remove_cgroups(dirs):
    for d in dirs:
        try:
            os.rmdir(d)
        except OSError:
            pass


def emit(obj):
    sys.stdout.write(json.dumps(obj) + "\n")
    sys.stdout.flush()


def main():
    req_text = sys.stdin.read()
    req = json.loads(req_text)
    subreaper = make_subreaper()
    parent = os.getppid()

    def on_term(signum, frame):
        n = kill_descendants()
        emit({"status": "cancelled", "leftovers_killed": n, "subreaper": subreaper})
        os._exit(143)
    signal.signal(signal.SIGTERM, on_term)

    def watch_parent():
        while not _done.wait(0.2):
            if os.getppid() != parent:
                kill_descendants()
                os._exit(70)
    threading.Thread(target=watch_parent, daemon=True).start()

    cg_procs, cg_dirs = make_cgroups(req) if (req.get("isolation") or {}).get("cgroups", True) else ([], [])
    req.setdefault("isolation", {})["cgroup_procs"] = cg_procs
    req_text = json.dumps(req)
    env = {"PATH": os.environ.get("PATH", os.defpath), "LANG": "C.UTF-8", "PYTHONDONTWRITEBYTECODE": "1",
           "HOME": req.get("workdir") or os.getcwd(), "TMPDIR": req.get("workdir") or os.getcwd()}
    child = subprocess.Popen([sys.executable, "-I", CHILD], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                             stderr=subprocess.PIPE, env=env, cwd=req.get("workdir") or None, text=True)
    bufs = {"out": [], "err": []}

    def drain(stream, key):
        for chunk in iter(lambda: stream.read(4096), ""):
            bufs[key].append(chunk)
    readers = [threading.Thread(target=drain, args=(child.stdout, "out"), daemon=True),
               threading.Thread(target=drain, args=(child.stderr, "err"), daemon=True)]
    for r in readers:
        r.start()
    child.stdin.write(req_text)
    child.stdin.close()
    # Wait for the handler PROCESS, not for pipe EOF: a forked daemon may hold the pipes open. The moment the
    # handler process exits (or the deadline passes) every descendant is killed, so nothing it started can act later.
    try:
        child.wait(timeout=float(req["timeout_s"]))
        timed_out = False
    except subprocess.TimeoutExpired:
        timed_out = True
    n = kill_descendants()
    _done.set()
    remove_cgroups(cg_dirs)
    for r in readers:
        r.join(2)
    if timed_out:
        emit({"status": "timeout", "leftovers_killed": n, "subreaper": subreaper})
        return
    out, err = "".join(bufs["out"]), "".join(bufs["err"])
    lines = [ln for ln in (out or "").splitlines() if ln.strip()]
    try:
        status = json.loads(lines[-1]) if lines else {"status": "crashed", "error": f"exit {child.returncode}"}
    except json.JSONDecodeError:
        status = {"status": "crashed", "error": "unparseable child output"}
    status.update({"leftovers_killed": n, "subreaper": subreaper, "child_exit": child.returncode,
                   "cgroups": cg_dirs,
                   "stderr_tail": (err or "")[-2000:]})
    emit(status)


if __name__ == "__main__":
    main()
