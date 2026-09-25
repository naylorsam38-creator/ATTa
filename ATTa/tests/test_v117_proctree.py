"""v117 (checklist E): a timed-out or cancelled deployment leaves NO process behind, and nothing else is touched.

    cd ATTa && python3 -m unittest tests.test_v117_proctree -v

Real processes throughout. Each test also starts an unrelated process first and checks it survives: the cleanup
must stop exactly the deployment's tree, never a neighbour (and never a pid that was reused)."""
import os, signal, subprocess, sys, time, unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from atta_testlib import tmpdir, wait_until  # noqa: E402
from adm import proc  # noqa: E402

LINUX = sys.platform.startswith("linux")


def alive(pid):
    s = proc._stat(pid)
    return s is not None and s[4] not in ("Z", "X")


def pids_with_marker(marker):
    out = []
    for pid in proc._all_pids():
        if marker in proc.cmdline(pid) and alive(pid):
            out.append(pid)
    return out


@unittest.skipUnless(LINUX, "ADM runs on Linux servers (reads /proc)")
class Tree(unittest.TestCase):
    def setUp(self):
        self.t = tmpdir("atta-proc-")
        self.marker = f"atta-proc-test-{os.getpid()}-{time.monotonic_ns()}"
        # A neighbour: started BEFORE the deploy by this same process. It must survive every cleanup.
        self.neighbour = subprocess.Popen(["sleep", "600"], start_new_session=True)

    def tearDown(self):
        for pid in pids_with_marker(self.marker):
            try:
                os.kill(pid, signal.SIGKILL)
            except OSError:
                pass
        self.assertTrue(alive(self.neighbour.pid), "the unrelated neighbour process was killed")
        self.neighbour.kill(); self.neighbour.wait()

    def sh(self, script):
        # The marker rides in argv so leftovers can be found (and cleaned) whatever happens.
        return ["bash", "-c", script, self.marker]

    def assertGone(self, res):
        self.assertTrue(res.stopped, f"survivors: {res.survivors}")
        self.assertEqual(wait_until(lambda: not pids_with_marker(self.marker), 5) or pids_with_marker(self.marker), True
                         if not pids_with_marker(self.marker) else pids_with_marker(self.marker))

    def test_normal_exit(self):
        res = proc.run_tree(self.sh("exit 3"), timeout=30)
        self.assertEqual((res.returncode, res.timed_out, res.leftovers), (3, False, []))
        self.assertTrue(res.stopped)

    def test_hanging_deployment_times_out_and_is_stopped(self):
        t0 = time.monotonic()
        res = proc.run_tree(self.sh(f"sleep 600 # {self.marker}"), timeout=1, grace=5)
        self.assertTrue(res.timed_out)
        self.assertLess(time.monotonic() - t0, 15)
        self.assertGone(res)

    def test_child_that_outlives_its_parent(self):
        # bash exits 0 at once, leaving a background child: it is still part of the deploy and is stopped.
        res = proc.run_tree(self.sh(f"(exec -a {self.marker}-bg sleep 600) & exit 0"), timeout=30, grace=5)
        self.assertEqual(res.returncode, 0)
        self.assertTrue(res.leftovers, "the leftover child was not noticed")
        self.assertFalse(res.ok, "a deploy that leaves a process running is not a clean success")
        self.assertGone(res)

    def test_descendant_that_escapes_with_setsid(self):
        # A daemon-style double fork into a NEW session: not in the group any more, re-parented to us (subreaper).
        script = f"(setsid bash -c 'exec -a {self.marker}-daemon sleep 600' &) ; sleep 600"
        res = proc.run_tree(self.sh(script), timeout=2, grace=5)
        self.assertTrue(res.timed_out)
        self.assertGone(res)

    def test_term_ignored_escalates_to_kill(self):
        res = proc.run_tree(self.sh(f"trap '' TERM; (exec -a {self.marker}-stubborn sleep 600) ; sleep 600"),
                            timeout=1, grace=1)
        self.assertTrue(res.timed_out)
        self.assertTrue(res.escalated)
        self.assertGone(res)

    def test_timeout_during_package_install_like_step(self):
        # A long "install" (python child of bash, itself with a child) killed mid-way.
        script = (f"python3 -c 'import subprocess,time; subprocess.Popen([\"sleep\",\"600\"]); time.sleep(600)' "
                  f"{self.marker}")
        res = proc.run_tree(self.sh(script), timeout=1.5, grace=5)
        self.assertTrue(res.timed_out)
        self.assertGone(res)

    def test_cancel_marks_interrupted(self):
        t0 = time.monotonic()
        res = proc.run_tree(self.sh("sleep 600"), timeout=60, cancel=lambda: time.monotonic() - t0 > 0.5, grace=5)
        self.assertTrue(res.interrupted)
        self.assertFalse(res.timed_out)
        self.assertGone(res)

    def test_on_start_reports_pid_and_group(self):
        seen = []
        res = proc.run_tree(self.sh("exit 0"), timeout=30, on_start=lambda pid, pgid: seen.append((pid, pgid)))
        self.assertEqual(seen, [(res.pid, res.pgid)])
        self.assertEqual(res.pid, res.pgid)            # its own process group (and session)

    def test_reused_pid_is_never_signalled(self):
        # A member recorded with a different start time is someone else now: stop_tree must leave it alone.
        time.sleep(0.05)   # /proc start times tick every 10 ms: the neighbour must be visibly older
        stranger = subprocess.Popen(["sleep", "600"], start_new_session=True)
        try:
            # Worst case on purpose: an empty baseline, so only the birth-time and identity rules protect the rest.
            tree = proc._Tree(stranger.pid, set())
            tree.members = {stranger.pid: (proc.start_ticks(stranger.pid) or 0) + 12345}
            tree.group_gone = True                     # its group number is not ours either
            res = proc.TreeResult()
            proc.stop_tree(tree, 0.5, res)
            self.assertTrue(alive(stranger.pid))
            self.assertEqual(res.signalled, [])
        finally:
            stranger.kill(); stranger.wait()

    def test_inherited_fd_keeps_a_lock_held_by_the_whole_tree(self):
        # The server-wide deploy lock is passed down: while ANY descendant lives, nobody else can take it.
        import fcntl
        lockf = self.t / "deploy.lock"
        fd = os.open(lockf, os.O_RDWR | os.O_CREAT, 0o600)
        fcntl.flock(fd, fcntl.LOCK_EX)
        res = proc.run_tree(self.sh(f"(exec -a {self.marker}-holder sleep 600) & exit 0"), timeout=30,
                            pass_fds=(fd,), grace=5)
        os.close(fd)
        self.assertGone(res)                         # the leftover (which held the lock) was stopped...
        fd2 = os.open(lockf, os.O_RDWR)
        try:
            fcntl.flock(fd2, fcntl.LOCK_EX | fcntl.LOCK_NB)  # ...so the lock is free again
        finally:
            os.close(fd2)


if __name__ == "__main__":
    unittest.main()
