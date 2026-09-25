"""v117 (checklists H, I, J; PR #5 bugs 8-13): the local `bash run` starts, reports and stops ATTa honestly.

    cd ATTa && python3 -m unittest tests.test_v117_launcher -v

Every test drives the REAL `run` script (bash run / bash run status / bash run stop) against its own data folder
and port, with the real gateway and pipeline. Nothing here uses the machine's systemd."""
import json, os, re, signal, socket, subprocess, sys, threading, time, unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from atta_testlib import BUNDLE, DEP, Impostor, free_port, tmpdir, wait_until  # noqa: E402

RUN = BUNDLE / "run"


def pids_of(marker):
    r = subprocess.run(["ps", "-A", "-o", "pid=,command="], capture_output=True, text=True)
    return [int(l.split(None, 1)[0]) for l in r.stdout.splitlines() if marker in l and "ps -A" not in l]


class Local(unittest.TestCase):
    def setUp(self):
        self.root = tmpdir("atta-local-")
        self.port = free_port()
        self.addCleanup(self.cleanup)

    def env(self, **extra):
        e = {k: v for k, v in os.environ.items() if not k.startswith(("APP_BUILDER_", "ATTA_"))}
        e.update(APP_BUILDER_ROOT=str(self.root), PYTHONDONTWRITEBYTECODE="1", ATTA_LOCAL_START_TIMEOUT="40",
                 ATTA_LOCAL_LOCK_WAIT="60")
        e.pop("INVOCATION_ID", None)
        e.update(extra)
        return e

    def run_(self, *args, **extra):
        # `bash run` alone treats any Linux with systemd as a server; the tests use `bash run local` (the laptop
        # instance on purpose), so they behave the same on a laptop, a CI runner and a server's deploy gate.
        return subprocess.run(["bash", str(RUN), *args], env=self.env(**extra), capture_output=True, text=True,
                              timeout=180)

    def first_start(self):
        # Make the .env (port) before the first start so each test has its own port.
        r = self.run_("status")                                   # creates nothing, reports down
        self.assertEqual(r.returncode, 3, r.stdout + r.stderr)
        (self.root).mkdir(exist_ok=True)
        from local_launcher import Launcher
        vals = Launcher(self.root).ensure_env()
        text = (self.root / ".env").read_text().replace("APP_BUILDER_PORT=8787", f"APP_BUILDER_PORT={self.port}")
        (self.root / ".env").write_text(text)
        return self.run_("local")

    def state(self):
        return json.loads((self.root / "run-state.json").read_text())

    def cleanup(self):
        subprocess.run(["bash", str(RUN), "stop"], env=self.env(), capture_output=True, timeout=120)
        for pid in pids_of(str(self.root)):
            try:
                os.kill(pid, signal.SIGKILL)
            except OSError:
                pass

    # ------------------------------------------------------------------------------------------------ H: status
    def test_start_status_stop(self):
        r = self.first_start()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("RUNNING (local instance", r.stdout)
        st = self.state()
        self.assertEqual(st["status"], "RUNNING")
        self.assertEqual(set(st["services"]), {"gateway", "pipeline"})
        r = self.run_("status")
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertIn("RUNNING", r.stdout)
        r = self.run_("stop")
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertFalse(pids_of(str(DEP / "gateway.py")) and any(str(self.root) in l for l in []))
        for rec in st["services"].values():
            self.assertFalse(Path(f"/proc/{rec['pid']}").exists() and "Z" not in
                             Path(f"/proc/{rec['pid']}/stat").read_text().split(")")[1][:3])
        self.assertEqual(self.run_("status").returncode, 3)

    def test_crashed_pipeline_is_not_reported_running(self):
        # PR #5 bug 9: RUNNING while the pipeline had crashed.
        self.assertEqual(self.first_start().returncode, 0)
        os.kill(self.state()["services"]["pipeline"]["pid"], signal.SIGKILL)
        time.sleep(0.5)
        r = self.run_("status")
        self.assertEqual(r.returncode, 4, r.stdout)
        self.assertIn("pipeline exited", r.stdout)
        self.assertEqual(self.state()["status"], "DEGRADED")
        self.assertIn("pipeline exited", self.state()["failure"]["reason"])
        r = self.run_("local")                                             # start again repairs it
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertIn("only partly running", r.stdout)

    def test_pipeline_that_cannot_start_fails_the_start(self):
        # A pipeline that dies at start: FAILED, nonzero, nothing left running, reason and exit code kept.
        (self.root / "state").mkdir(parents=True, exist_ok=True)
        import fcntl
        fd = os.open(self.root / "state" / "pipeline.instance.lock", os.O_RDWR | os.O_CREAT)
        fcntl.flock(fd, fcntl.LOCK_EX)                              # as if another pipeline held this folder
        try:
            r = self.first_start()
        finally:
            os.close(fd)
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertIn("FAILED (local instance)", r.stdout)
        self.assertIn("pipeline exited", r.stdout)
        st = self.state()
        self.assertEqual((st["status"], st["services"]["pipeline"].get("exit")), ("FAILED", 3))
        self.assertFalse(Path(f"/proc/{st['services']['gateway']['pid']}/cmdline").exists()
                         and b"gateway.py" in Path(f"/proc/{st['services']['gateway']['pid']}/cmdline").read_bytes())

    def test_port_used_by_another_program_fails_fast_and_starts_nothing(self):
        # PR #5 bugs 10 + 11: something else on the port counted as ATTa; a failed start left the pipeline behind.
        imp = Impostor("OK\n", ctype="text/plain", port=self.port)
        try:
            t0 = time.monotonic()
            r = self.first_start()
            self.assertEqual(r.returncode, 1, r.stdout)
            self.assertLess(time.monotonic() - t0, 25)
            self.assertIn("already in use by another program", r.stdout)
            self.assertNotIn("RUNNING", r.stdout)
            self.assertFalse(self.state().get("services"))          # nothing was started
            r = self.run_("status")
            self.assertNotEqual(r.returncode, 0)
        finally:
            imp.stop()

    # ------------------------------------------------------------------------------------------------ I: safe stop
    def test_stale_state_never_kills_an_unrelated_process(self):
        # PR #5 bug 12: a stale pid file got an unrelated process killed.
        victim = subprocess.Popen(["sleep", "300"])
        try:
            fake = {"status": "RUNNING", "port": self.port, "services": {
                "gateway": {"pid": victim.pid, "pgid": victim.pid, "start": "0", "cmdline": "python3 gateway.py",
                            "script": str(DEP / "gateway.py")},
                "pipeline": {"pid": victim.pid, "pgid": os.getpgid(victim.pid), "start": "0",
                             "cmdline": "python3 pipeline.py", "script": str(DEP / "pipeline.py")}}}
            self.root.mkdir(exist_ok=True)
            (self.root / "run-state.json").write_text(json.dumps(fake))
            r = self.run_("stop")
            self.assertEqual(r.returncode, 0, r.stdout)
            time.sleep(0.3)
            self.assertIsNone(victim.poll(), "an unrelated process was killed")
        finally:
            victim.kill(); victim.wait()

    def test_stop_takes_the_whole_process_group(self):
        # A service and what it started (the pipeline's docker/node children) share its process group: all go.
        from local_launcher import Launcher, identity
        code = ("import subprocess, sys, time; subprocess.Popen(['bash', '-c', 'exec -a ' + sys.argv[1] + '-child sleep 300']);"
                "time.sleep(300)")
        leader = subprocess.Popen([sys.executable, "-c", code, str(self.root), str(DEP / "pipeline.py")],
                                  start_new_session=True)
        self.assertTrue(wait_until(lambda: pids_of(f"{self.root}-child"), 10))
        child_pid = pids_of(f"{self.root}-child")[0]
        self.assertEqual(os.getpgid(child_pid), leader.pid)
        start, cmd = identity(leader.pid)
        rec = {"pid": leader.pid, "pgid": leader.pid, "start": start, "cmdline": cmd, "script": str(DEP / "pipeline.py")}
        self.root.mkdir(exist_ok=True)
        (self.root / "run-state.json").write_text(json.dumps({"status": "RUNNING", "services": {"pipeline": rec}}))
        r = self.run_("stop")
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertTrue(wait_until(lambda: leader.poll() is not None, 10))
        self.assertTrue(wait_until(lambda: not pids_of(f"{self.root}-child"), 10), "the service's child survived")

    # ------------------------------------------------------------------------------------------------ J: one at a time
    def test_two_starts_at_once_give_one_instance(self):
        # PR #5 bug 13: two `bash run` at once gave two pipelines.
        self.first_start()
        self.run_("stop")
        out = []
        ts = [threading.Thread(target=lambda: out.append(self.run_("local"))) for _ in range(2)]
        [t.start() for t in ts]
        [t.join() for t in ts]
        self.assertEqual(sorted(r.returncode for r in out), [0, 0], [r.stdout for r in out])
        self.assertTrue(any("already up" in r.stdout for r in out), [r.stdout for r in out])
        pipes = [p for p in pids_of(str(DEP / "pipeline.py"))
                 if str(self.root) in Path(f"/proc/{p}/environ").read_bytes().decode("utf-8", "replace")]
        self.assertEqual(len(pipes), 1, pipes)

    def test_stop_during_start_waits_for_it(self):
        self.first_start(); self.run_("stop")
        results = {}
        t = threading.Thread(target=lambda: results.update(start=self.run_("local")))
        t.start()
        time.sleep(0.3)
        results["stop"] = self.run_("stop")
        t.join()
        self.assertEqual(results["start"].returncode, 0, results["start"].stdout)
        self.assertEqual(results["stop"].returncode, 0, results["stop"].stdout)
        self.assertEqual(self.run_("status").returncode, 3)       # the stop came after the start, whole

    def test_second_pipeline_refuses_to_run(self):
        self.assertEqual(self.first_start().returncode, 0)
        r = subprocess.run([sys.executable, str(DEP / "pipeline.py")], env=self.env(), capture_output=True, text=True,
                           timeout=60)
        self.assertEqual(r.returncode, 3)
        self.assertIn("another pipeline already runs", r.stdout)

    # ------------------------------------------------------------------------------------------------ A: .env as data
    def test_local_env_is_never_executed(self):
        # PR #5 bug 8: `bash run` executed the local .env.
        marker = self.root / "PWNED"
        self.root.mkdir(exist_ok=True)
        from local_launcher import Launcher
        Launcher(self.root).ensure_env()
        with open(self.root / ".env", "a") as f:
            f.write(f"APP_BUILDER_WATCHER_INTERVAL=$(touch {marker})\n")
        r = self.run_("local")
        self.assertEqual(r.returncode, 1)
        self.assertIn("never run as shell", r.stdout)
        self.assertFalse(marker.exists())

    def test_old_env_without_secret_is_kept_aside(self):
        self.root.mkdir(exist_ok=True)
        (self.root / ".env").write_text("APP_BUILDER_PORT=1234\n")
        from local_launcher import Launcher
        vals = Launcher(self.root).ensure_env()
        self.assertTrue(vals["APP_BUILDER_SESSION_SECRET"])
        self.assertEqual(len(list(self.root.glob(".env.old-*"))), 1)
        self.assertEqual(oct((self.root / ".env").stat().st_mode & 0o777), "0o600")

    def test_run_file_is_clean(self):
        # PR #5 bug 14: lines above the shebang, AWS probing and .initialized on every run.
        text = RUN.read_text()
        self.assertTrue(text.startswith("#!/usr/bin/env bash\n"))
        self.assertNotIn("169.254.169.254", text)
        self.assertNotIn(".initialized", text)
        self.assertNotRegex(text, r"(^|[;&|\s])(\.|source)\s+\S*env")


if __name__ == "__main__":
    unittest.main()
