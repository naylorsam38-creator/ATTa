"""The server-wide deploy lock (v117, checklist F).

One file, one kernel lock (flock), shared by everything that can change what is live: deployd, deployctl, and a
`bash run` started by hand (bootstrap.sh takes the SAME lock unless it inherited it from ADM).

  - The lock fd is passed down to the installer, so it is held by the whole deploy tree: if deployd/deployctl is
    killed, a still-running installer keeps the lock and nothing else can start until it is gone.
  - It can't go stale: the kernel releases it when the last process holding it exits. There is nothing to
    "break" and no timeout that could hand the server to a second deploy while the first is still running.
  - Who holds it is written beside it (deploy.lock.owner.json): deployment id, pid, the pid's start time,
    hostname, when. That record is information only. It is written only by the process that got the lock,
    and removed only by that same owner (checked by deployment id AND pid AND start time), so an old owner
    record left by a killed deploy is recognised as stale (the lock itself is free) and reported, never trusted.
"""
from __future__ import annotations
import fcntl, json, os, socket, time
from datetime import datetime, timezone
from pathlib import Path


class Busy(RuntimeError):
    """Another deployment holds the lock. .owner has what its owner record says (may be None)."""

    def __init__(self, msg, owner=None):
        super().__init__(msg)
        self.owner = owner


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _start_ticks(pid):
    try:
        with open(f"/proc/{pid}/stat", "rb") as f:
            data = f.read().decode("utf-8", "replace")
        return int(data[data.rfind(")") + 2:].split()[19])
    except (OSError, ValueError, IndexError):
        return None


def owner_path(lock_path) -> Path:
    return Path(str(lock_path) + ".owner.json")


def read_owner(lock_path):
    try:
        return json.loads(owner_path(lock_path).read_text())
    except (OSError, ValueError):
        return None


def owner_alive(owner) -> bool:
    """True only if the recorded pid is running AND is the same process (same start time, same host)."""
    if not isinstance(owner, dict) or owner.get("hostname") != socket.gethostname():
        return False
    try:
        pid = int(owner.get("pid"))
    except (TypeError, ValueError):
        return False
    st = _start_ticks(pid)
    return st is not None and st == owner.get("pid_start")


class DeployLock:
    """with DeployLock(path, deployment_id) as lk: ...  (raises Busy if held). lk.fd can be passed to children."""

    def __init__(self, path, deployment_id, *, wait=0.0):
        self.path = Path(path)
        self.deployment_id = str(deployment_id)
        self.wait = wait
        self.fd = None
        self.stale_owner = None      # an owner record left behind by a deploy that is gone

    def acquire(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(self.path, os.O_RDWR | os.O_CREAT | os.O_CLOEXEC, 0o600)
        end = time.monotonic() + self.wait
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= end:
                    os.close(fd)
                    owner = read_owner(self.path)
                    who = (f"deployment {owner.get('deployment_id')} (pid {owner.get('pid')} on {owner.get('hostname')}, "
                           f"since {owner.get('acquired_at')})") if owner else "another deployment"
                    raise Busy(f"{who} holds the deploy lock; nothing was started", owner)
                time.sleep(0.2)
        self.fd = fd
        prev = read_owner(self.path)
        if prev and prev.get("deployment_id") != self.deployment_id:
            # We hold the kernel lock, so whoever wrote this is gone: a killed or crashed deploy.
            self.stale_owner = prev
        self._write_owner()
        return self

    def _write_owner(self):
        me = os.getpid()
        rec = {"deployment_id": self.deployment_id, "pid": me, "pid_start": _start_ticks(me),
               "hostname": socket.gethostname(), "acquired_at": _now()}
        p = owner_path(self.path)
        tmp = p.with_name(p.name + f".{me}.tmp")
        tmp.write_text(json.dumps(rec, indent=2) + "\n")
        os.chmod(tmp, 0o600)
        os.replace(tmp, p)

    def is_mine(self, owner) -> bool:
        me = os.getpid()
        return (isinstance(owner, dict) and owner.get("deployment_id") == self.deployment_id
                and owner.get("pid") == me and owner.get("pid_start") == _start_ticks(me))

    def release(self):
        if self.fd is None:
            return
        try:
            if self.is_mine(read_owner(self.path)):       # remove only our own record
                owner_path(self.path).unlink(missing_ok=True)
        finally:
            fcntl.flock(self.fd, fcntl.LOCK_UN)
            os.close(self.fd)
            self.fd = None

    def __enter__(self):
        return self.acquire()

    def __exit__(self, *exc):
        self.release()


def holder(lock_path):
    """(held, owner record) without taking the lock: for status displays."""
    p = Path(lock_path)
    if not p.exists():
        return False, None
    fd = os.open(p, os.O_RDWR | os.O_CLOEXEC)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        fcntl.flock(fd, fcntl.LOCK_UN)
        return False, read_owner(p)
    except BlockingIOError:
        return True, read_owner(p)
    finally:
        os.close(fd)


def write_owner_for(lock_path, deployment_id, pid):
    """bootstrap.sh (a hand-typed `bash run`) holds the lock from bash itself: record bash's pid as the owner, so
    nobody mistakes the running install for an abandoned one. Call only while that process holds the lock."""
    pid = int(pid)
    rec = {"deployment_id": str(deployment_id), "pid": pid, "pid_start": _start_ticks(pid),
           "hostname": socket.gethostname(), "acquired_at": _now()}
    p = owner_path(lock_path)
    tmp = p.with_name(p.name + f".{os.getpid()}.tmp")
    tmp.write_text(json.dumps(rec, indent=2) + "\n")
    os.chmod(tmp, 0o600)
    os.replace(tmp, p)
    return rec


if __name__ == "__main__":
    import sys
    if len(sys.argv) == 5 and sys.argv[1] == "owner":
        write_owner_for(sys.argv[2], sys.argv[3], sys.argv[4])
        sys.exit(0)
    print("usage: lock.py owner LOCKFILE DEPLOYMENT_ID PID", file=sys.stderr)
    sys.exit(2)
