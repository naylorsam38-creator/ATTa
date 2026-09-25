"""Run a command as a process TREE that can be stopped completely (v117, checklist E).

subprocess.run(timeout=...) only kills the direct child: with `bash run` that is bash, while the dnf/pip/docker it
started keep changing the server after ADM has "given up" and rolled back. run_tree() instead:

  - starts the command in its own session and process group (start_new_session);
  - makes this process a child subreaper (Linux prctl), so descendants that lose their parent are re-parented
    HERE instead of to init, and can still be found;
  - samples the tree while it runs, remembering every descendant by (pid, start time) so a pid that is reused
    by an unrelated process later is never signalled;
  - on timeout or cancellation: SIGTERM to every member (group, session, sampled and re-parented descendants),
    a grace period, then SIGKILL, then waits and CONFIRMS none remain. The caller rolls back only when
    result.stopped is True.

Linux only (reads /proc): ADM runs on the server. The local launcher has its own, portable, identity checks.
"""
from __future__ import annotations
import ctypes, os, signal, subprocess, time
from dataclasses import dataclass, field

PR_SET_CHILD_SUBREAPER = 36
SAMPLE_SECONDS = 0.25


@dataclass
class TreeResult:
    returncode: int | None = None
    timed_out: bool = False
    interrupted: bool = False
    pid: int = 0
    pgid: int = 0
    seconds: float = 0.0
    signalled: list = field(default_factory=list)   # [(pid, cmdline)] that got SIGTERM/SIGKILL
    escalated: bool = False                          # SIGKILL was needed
    survivors: list = field(default_factory=list)   # [(pid, cmdline)] still alive after cleanup
    leftovers: list = field(default_factory=list)   # descendants still running after a NORMAL exit (then stopped)

    @property
    def stopped(self) -> bool:
        """True when nothing from this tree is left running (safe to roll back / start another deploy)."""
        return not self.survivors

    @property
    def ok(self) -> bool:
        # A run that left something running in the background is not clean, even though it was stopped.
        return (self.returncode == 0 and not self.timed_out and not self.interrupted and self.stopped
                and not self.leftovers)


class Cancelled(Exception):
    """Raised by a cancel() callback to stop the tree (e.g. deployd is being stopped)."""


_subreaper = None


def become_subreaper() -> bool:
    global _subreaper
    if _subreaper is None:
        try:
            libc = ctypes.CDLL(None, use_errno=True)
            _subreaper = libc.prctl(PR_SET_CHILD_SUBREAPER, 1, 0, 0, 0) == 0
        except (OSError, AttributeError):
            _subreaper = False
    return _subreaper


def _stat(pid):
    """(ppid, pgid, sid, start_ticks, state) from /proc/<pid>/stat, or None if gone."""
    try:
        with open(f"/proc/{pid}/stat", "rb") as f:
            data = f.read().decode("utf-8", "replace")
    except OSError:
        return None
    rest = data[data.rfind(")") + 2:].split()
    try:
        return int(rest[1]), int(rest[2]), int(rest[3]), int(rest[19]), rest[0]
    except (IndexError, ValueError):
        return None


def start_ticks(pid):
    s = _stat(pid)
    return s[3] if s else None


def cmdline(pid):
    try:
        with open(f"/proc/{pid}/cmdline", "rb") as f:
            return f.read().replace(b"\0", b" ").decode("utf-8", "replace").strip()[:200]
    except OSError:
        return ""


def _all_pids():
    for d in os.listdir("/proc"):
        if d.isdigit():
            yield int(d)


def _snapshot():
    """{pid: (ppid, pgid, sid, start, state)} of every process."""
    out = {}
    for pid in _all_pids():
        s = _stat(pid)
        if s:
            out[pid] = s
    return out


def _descendants(snap, root):
    kids = {}
    for pid, s in snap.items():
        kids.setdefault(s[0], []).append(pid)
    seen, todo = set(), [root]
    while todo:
        for c in kids.get(todo.pop(), ()):
            if c not in seen:
                seen.add(c); todo.append(c)
    return seen


class _Tree:
    """Everything that belongs to one run: (pid -> start ticks), gathered by sampling."""

    def __init__(self, root_pid, baseline_children):
        self.root = root_pid
        self.me = os.getpid()
        self.baseline = baseline_children       # our own children that existed before: never part of the tree
        self.members = {root_pid: start_ticks(root_pid)}
        self.born = self.members[root_pid] or 0  # an orphan re-parented to us counts only if born after the root
        # Linux never reuses a pid while any process still has it as its group or session id. Once the group is
        # seen empty, the number COULD come back as someone else's group: from then on the group rules are off.
        self.group_gone = False

    def sample(self):
        snap = _snapshot()
        for pid in _descendants(snap, self.root):
            self.members.setdefault(pid, snap[pid][3])
        if not self.group_gone and not any(s[1] == self.root or s[2] == self.root for s in snap.values()):
            self.group_gone = True
        for pid, s in snap.items():
            if pid == self.me:
                continue
            same_group = not self.group_gone and (s[1] == self.root or s[2] == self.root)
            orphan_here = (s[0] == self.me and pid != self.root and pid not in self.baseline
                           and s[3] >= self.born)
            if same_group or orphan_here:
                self.members.setdefault(pid, s[3])
                for d in _descendants(snap, pid):
                    self.members.setdefault(d, snap[d][3])
        return snap

    def alive(self):
        """Members still running, identity-checked (a reused pid has a different start time)."""
        out = []
        for pid, st in list(self.members.items()):
            s = _stat(pid)
            if s is None or s[4] in ("Z", "X") or (st is not None and s[3] != st):
                continue
            out.append(pid)
        return out


def _reap(pids):
    me = os.getpid()
    for pid in pids:
        s = _stat(pid)
        if s and s[0] == me and s[4] in ("Z", "X"):
            try:
                os.waitpid(pid, os.WNOHANG)
            except ChildProcessError:
                pass


def stop_tree(tree: _Tree, grace: float, result: TreeResult, popen=None):
    """SIGTERM every member, wait `grace`, SIGKILL the rest, wait, record survivors."""
    tree.sample()
    names = {}

    def signal_all(sig):
        victims = tree.alive()
        for pid in victims:
            names.setdefault(pid, cmdline(pid))
            try:
                os.kill(pid, sig)
            except (ProcessLookupError, PermissionError):
                pass
        if not tree.group_gone:
            try:
                os.killpg(tree.root, sig)      # the group itself (catches members not yet sampled)
            except (ProcessLookupError, PermissionError):
                pass
        return victims

    victims = signal_all(signal.SIGTERM)
    end = time.monotonic() + grace
    while time.monotonic() < end:
        if popen is not None:
            popen.poll()
        tree.sample()
        _reap(tree.members)
        if not tree.alive():
            break
        time.sleep(0.1)
    if tree.alive():
        result.escalated = True
        victims += signal_all(signal.SIGKILL)
        end = time.monotonic() + 10
        while time.monotonic() < end and tree.alive():
            if popen is not None:
                popen.poll()
            _reap(tree.members)
            time.sleep(0.1)
    if popen is not None:
        try:
            popen.wait(timeout=5)
        except subprocess.TimeoutExpired:
            pass
    _reap(tree.members)
    result.signalled = sorted({(p, names.get(p, "")) for p in victims})
    result.survivors = [(p, cmdline(p)) for p in tree.alive()]


def run_tree(cmd, *, cwd=None, env=None, stdout=None, stderr=subprocess.STDOUT, timeout=None, grace=20.0,
             pass_fds=(), cancel=None, on_start=None, on_stop=None) -> TreeResult:
    """Run cmd to completion, or stop its whole tree on timeout / cancel. Never raises for the child's failure.

    cancel: optional callable; if it returns True (or raises Cancelled) the tree is stopped as `interrupted`.
    on_start: optional callable(pid, pgid) — e.g. to record them in the deployment record.
    on_stop: optional callable("timed_out" | "interrupted"), called BEFORE the tree gets any signal — so the caller's
    record says why first, and nothing the signal wakes up in the tree can get in ahead of it."""
    become_subreaper()
    me = os.getpid()
    baseline = {pid for pid, s in _snapshot().items() if s[0] == me}
    t0 = time.monotonic()
    p = subprocess.Popen(cmd, cwd=cwd, env=env, stdout=stdout, stderr=stderr, stdin=subprocess.DEVNULL,
                         start_new_session=True, pass_fds=pass_fds, close_fds=True)
    res = TreeResult(pid=p.pid, pgid=p.pid)
    tree = _Tree(p.pid, baseline)
    if on_start:
        on_start(p.pid, p.pid)
    try:
        while True:
            tree.sample()
            if p.poll() is not None:
                break
            if timeout is not None and time.monotonic() - t0 > timeout:
                res.timed_out = True
                break
            try:
                if cancel is not None and cancel():
                    res.interrupted = True
                    break
            except Cancelled:
                res.interrupted = True
                break
            time.sleep(SAMPLE_SECONDS)
    except BaseException:
        # KeyboardInterrupt / SystemExit in the caller: never leave the tree behind.
        res.interrupted = True
        _notify(on_stop, "interrupted")
        stop_tree(tree, grace, res, p)
        raise
    if res.timed_out or res.interrupted:
        _notify(on_stop, "timed_out" if res.timed_out else "interrupted")
        stop_tree(tree, grace, res, p)
    else:
        # Normal exit: anything it left running in the background is part of the deploy too. Stop it.
        tree.sample()
        left = [pid for pid in tree.alive() if pid != p.pid]
        if left:
            res.leftovers = [(pid, cmdline(pid)) for pid in left]
            stop_tree(tree, grace, res, None)
    res.returncode = p.returncode if p.returncode is not None else p.poll()
    res.seconds = round(time.monotonic() - t0, 2)
    return res


def _notify(on_stop, why):
    """on_stop must never keep the tree from being stopped."""
    if on_stop is None:
        return
    try:
        on_stop(why)
    except Exception:
        pass


def find_by_env(name, value):
    """Processes whose environment holds NAME=VALUE exactly (root reads every /proc/<pid>/environ).
    Every process of a deploy inherits ATTA_DEPLOYMENT_ID, so after a crash this finds whatever survived it
    even if it left the group, the session and our subreaper (services started via systemd don't inherit it)."""
    needle = f"{name}={value}".encode()
    me = os.getpid()
    out = []
    for pid in _all_pids():
        if pid == me:
            continue
        try:
            with open(f"/proc/{pid}/environ", "rb") as f:
                env = f.read().split(b"\0")
        except OSError:
            continue
        if needle in env:
            s = _stat(pid)
            if s and s[4] not in ("Z", "X"):
                out.append(pid)
    return out


def stop_pids(pids, grace=10.0) -> TreeResult:
    """Stop a set of processes found some other way (find_by_env): TERM, grace, KILL, confirm. Identity by start time."""
    res = TreeResult()
    tree = _Tree.__new__(_Tree)
    tree.root, tree.me, tree.baseline, tree.group_gone, tree.born = -1, os.getpid(), set(), True, 0
    tree.members = {p: start_ticks(p) for p in pids}
    tree.sample = lambda: None
    stop_tree(tree, grace, res)
    return res
