"""v121.2: every app is discovered and tested as its own isolated job, in parallel; one app's failure never
stops, corrupts or hides another's; evidence is bound to its app, run and revision; the fleet writes an
aggregate report. These tests run the REAL runner, watcher, pipeline and resilience code. What they replace
is only what needs a Docker host: the per-app start+check (qualify_app / run_app / start_proxy / down) is a
stand-in that behaves like a real job (sleeps, crashes, hangs, changes the app) so the fleet machinery around
it is exercised for real. The live Docker path is covered by the existing suites and the live run.
Run: python3 -m pytest -q tests/test_v121_2_parallel_isolation.py
"""
import http.server, importlib, json, os, shutil, socket, subprocess, sys, tempfile, threading, time, unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
DEP = HERE / "04-deployment"
sys.path.insert(0, str(DEP))

import resilience  # noqa: E402  (no ROOT dependency)
from resilience import (app_revision, code_revision, deployment_revision, handoff_state, levels,  # noqa: E402
                        revision_stable, safe_http_probe, safe_tcp_probe)

MODULES = ("app_runner", "system_watcher", "journey_author", "pipeline", "builds", "coolify_handoff",
           "maintenance", "rule_lifecycle", "known_fixes", "accounts", "intake")


def _git(d, *args):
    subprocess.run(["git", "-C", str(d), *args], check=True, capture_output=True,
                   env={**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t",
                        "GIT_COMMITTER_EMAIL": "t@t"})


def make_app(lib: Path, name: str, qualification: str = "web") -> Path:
    d = lib / name
    (d / ".ui-capability").mkdir(parents=True)
    (d / ".ui-capability" / "run-ui.sh").write_text("#!/bin/sh\n")
    (d / ".ui-capability" / "skin.css").write_text("body{}\n")
    (d / "README.md").write_text(name + "\n")
    _git(d, "init", "-q"); _git(d, "add", "-A"); _git(d, "commit", "-qm", "init")
    (d / ".atta-intake.json").write_text(json.dumps({"app": name, "status": "READY", "qualification": qualification}))
    return d


class _Root(unittest.TestCase):
    """A fresh APP_BUILDER_ROOT and freshly imported modules for every test."""
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        os.environ["APP_BUILDER_ROOT"] = str(self.root)
        os.environ["APP_BUILDER_SESSION_SECRET"] = "x"
        for m in MODULES:
            sys.modules.pop(m, None)
        self.ar = importlib.import_module("app_runner")
        self.w = importlib.import_module("system_watcher")
        self.lib = self.ar.LIB
        self.ar.START_FREE_MEMORY = 0.0
        self.ar.START_FREE_DISK_GB = 0.0
        self.ar._POOL_POLL = 0.05
        self.ar._GATE_POLL = 0.05
        self.sh_calls = []
        self.ar.sh = lambda cmd, timeout=120, env=None, cwd=None: (self.sh_calls.append(cmd) or (0, ""))
        self.ar.down = lambda app, prune=None: "stopped"
        self.logs = []
        self.log = self.logs.append

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    # A stand-in for one app's start + six-stage check: a real watcher-shaped PASS unless told otherwise.
    def passing(self, app):
        st = {k: self.w.stage() for k in ("1 INSTALLED", "2 APP_UP", "3 PROXY_UP", "4 SKIN", "5 HOOK", "6 CLEAN")}
        r = self.w.result(self.ar.safe_id(app), st)
        r["runner"] = {"started": True, "profile": "web", "attempts": []}
        r["observations"] = {"app_http": {"tcp_connected": True, "http_status": 200}}
        return r


# ------------------------------------------------------------------------------------------ revision identity
class RevisionIdentity(unittest.TestCase):
    def test_container_layout_is_never_unknown(self):
        """Under Coolify, APP_BUILDER_ROOT holds no release.json and no adm/current. v121.1 returned "unknown"
        there, and revision_stable("unknown", "unknown") is False: every app would have been UNVERIFIED."""
        root = Path(tempfile.mkdtemp())
        try:
            a = deployment_revision(root); b = deployment_revision(root)
            self.assertNotEqual(a, "unknown")
            self.assertIn("code:", a)
            self.assertTrue(revision_stable(a, b))
        finally:
            shutil.rmtree(root)

    def test_code_change_is_a_new_revision(self):
        code = Path(tempfile.mkdtemp()) / "04-deployment"
        try:
            code.mkdir(); (code.parent / "release.json").write_text('{"version":"x"}')
            (code / "a.py").write_text("x = 1\n")
            r1 = code_revision(code)
            (code / "a.py").write_text("x = 2\n")
            self.assertNotEqual(r1, code_revision(code))
            r2 = code_revision(code)
            (code.parent / "release.json").write_text('{"version":"y"}')
            self.assertNotEqual(r2, code_revision(code))
            self.assertEqual(code_revision(code), code_revision(code))
        finally:
            shutil.rmtree(code.parent)

    def test_adm_release_still_changes_the_revision(self):
        root = Path(tempfile.mkdtemp())
        try:
            (root / "adm" / "current").mkdir(parents=True)
            (root / "adm" / "current" / "release.json").write_text('{"version":"a"}')
            a = deployment_revision(root)
            (root / "adm" / "current" / "release.json").write_text('{"version":"b"}')
            self.assertFalse(revision_stable(a, deployment_revision(root)))
        finally:
            shutil.rmtree(root)

    def test_app_revision_sees_source_changes_not_runner_env_files(self):
        lib = Path(tempfile.mkdtemp())
        try:
            d = make_app(lib, "demo")
            r0 = app_revision(d)
            (d / ".env").write_text("A=1\n")               # the runner does this while starting an app
            (d / "sub").mkdir(); (d / "sub" / ".env").write_text("B=2\n")
            self.assertEqual(r0, app_revision(d))
            (d / "README.md").write_text("new\n"); _git(d, "commit", "-qam", "change")
            r1 = app_revision(d)
            self.assertNotEqual(r0, r1)                    # new commit = new app revision
            (d / ".ui-capability" / "skin.css").write_text("body{color:red}\n")
            r2 = app_revision(d)
            self.assertNotEqual(r1, r2)                    # new skin = what stages 4/5 compare changed
            (d / ".atta-intake.json").write_text('{"status":"READY","changes":["x"]}')
            self.assertNotEqual(r2, app_revision(d))       # intake changed the code
            self.assertEqual(app_revision(lib / "nope"), "missing")
        finally:
            shutil.rmtree(lib)


# ------------------------------------------------------------------------------------------ states + ladder
class OutcomeStates(unittest.TestCase):
    def r(self, **kw):
        base = {"app": "x", "verdict": "PASS", "broken_at": None, "code": None,
                "stages": {"6 CLEAN": {"status": "OK"}}, "runner": {"started": True},
                "verification": {"status": "CURRENT", "passed_checks": True}}
        base.update(kw)
        return base

    def test_every_state(self):
        self.assertEqual(handoff_state(None), "NOT_STARTED")
        self.assertEqual(handoff_state(self.r()), "QUALIFIED")
        self.assertEqual(handoff_state(self.r(verification={})), "INCONCLUSIVE")   # no barrier = no pass
        self.assertEqual(handoff_state(self.r(verdict="UNVERIFIED", broken_at="6 CLEAN",
                                              verification={"status": "UNVERIFIED"})), "UNVERIFIED")
        self.assertEqual(handoff_state(self.r(broken_at="2 APP_UP", code="RUNNER_EXHAUSTED",
                                              runner={"started": False, "attempts": [{"rule": "build.failed"}]})), "START_FAILED")
        self.assertEqual(handoff_state(self.r(broken_at="2 APP_UP", code="RUNNER_EXHAUSTED",
                                              runner={"started": False, "attempts": [{"rule": "app.still_starting"}]})), "TIMEOUT")
        self.assertEqual(handoff_state(self.r(broken_at="2 APP_UP", code="RUNNER_EXHAUSTED",
                                              runner={"started": False, "attempts": [{"rule": "budget.spent"}, {"rule": "x"}]})), "TIMEOUT")
        self.assertEqual(handoff_state(self.r(broken_at="2 APP_UP", code="TIMEOUT")), "TIMEOUT")
        self.assertEqual(handoff_state(self.r(broken_at="2 APP_UP", code="WORKER_TIMEOUT")), "TIMEOUT")
        self.assertEqual(handoff_state(self.r(broken_at="2 APP_UP", code="RUNNER_EXCEPTION")), "INCONCLUSIVE")
        self.assertEqual(handoff_state(self.r(broken_at="6 CLEAN", code="PLAYWRIGHT_UNAVAILABLE")), "INCONCLUSIVE")
        self.assertEqual(handoff_state(self.r(broken_at="4 SKIN", code="NO_LINK")), "FAILED")
        self.assertEqual(handoff_state(self.r(stages={"6 CLEAN": {"status": "NOT_RUN"}})), "INCONCLUSIVE")
        for s in ("NOT_STARTED", "START_FAILED", "TIMEOUT", "INCONCLUSIVE", "FAILED", "QUALIFIED", "UNVERIFIED"):
            self.assertIn(s, resilience.STATES)

    def test_ladder_rungs_are_separate_observations(self):
        # TCP accepted but nothing that speaks HTTP: reachable, no HTTP response, not healthy.
        # The probe connects twice: a bare TCP connect, then the HTTP request. Answer every connection with junk.
        srv = socket.socket(); srv.bind(("127.0.0.1", 0)); srv.listen(4); port = srv.getsockname()[1]
        def junk():
            for _ in range(2):
                c, _ = srv.accept()
                try:
                    if c.recv(1024):
                        c.sendall(b"\x00\x01garbage\r\n\r\n")
                finally:
                    c.close()
        th = threading.Thread(target=junk, daemon=True); th.start()
        try:
            obs = safe_http_probe(f"http://127.0.0.1:{port}/", timeout=3)
        finally:
            th.join(3); srv.close()
        self.assertTrue(obs["tcp_connected"])
        self.assertIsNone(obs.get("http_status"))
        self.assertEqual(obs["classification"], "HTTP_PROTOCOL_MISMATCH")
        lv = levels({"broken_at": "2 APP_UP", "code": "X", "stages": {"2 APP_UP": {"status": "FAIL"}},
                     "runner": {"started": True}, "observations": {"app_http": obs}})
        self.assertEqual((lv["tcp_reachable"], lv["http_response"], lv["healthy"], lv["qualified"], lv["verified"]),
                         (True, False, False, False, False))
        # HTTP 500 answered: a response, still not healthy.
        lv = levels({"broken_at": "2 APP_UP", "code": "UPSTREAM_5XX", "stages": {"2 APP_UP": {"status": "FAIL"}},
                     "runner": {"started": True}, "observations": {"app_http": {"tcp_connected": True, "http_status": 500}}})
        self.assertEqual((lv["tcp_reachable"], lv["http_response"], lv["healthy"]), (True, True, False))
        # Every stage passed but the barrier did not hold: qualified checks, NOT verified.
        ok = {k: {"status": "OK"} for k in ("2 APP_UP", "3 PROXY_UP", "6 CLEAN")}
        lv = levels({"broken_at": "6 CLEAN", "verdict": "UNVERIFIED", "stages": ok, "runner": {"started": True},
                     "verification": {"status": "UNVERIFIED", "passed_checks": True},
                     "observations": {"app_http": {"tcp_connected": True, "http_status": 200}}})
        self.assertEqual((lv["healthy"], lv["qualified"], lv["verified"]), (True, True, False))
        # Not listening at all.
        s = socket.socket(); s.bind(("127.0.0.1", 0)); p = s.getsockname()[1]; s.close()
        self.assertEqual(safe_tcp_probe("127.0.0.1", p, 1)["classification"], "ENDPOINT_NOT_LISTENING")


# ------------------------------------------------------------------------------------------ the fleet
class Fleet(_Root):
    def apps(self, *names):
        for n in names:
            make_app(self.lib, n)
        return list(names)

    def test_every_app_runs_at_the_same_time_by_default(self):
        names = self.apps("a1", "a2", "a3", "a4", "a5", "a6")
        live, peak, lock = [0], [0], threading.Lock()
        def fake(app, log=print, **kw):
            with lock:
                live[0] += 1; peak[0] = max(peak[0], live[0])
            time.sleep(0.4)
            with lock:
                live[0] -= 1
            return self.passing(app)
        self.ar.qualify_app = fake
        t0 = time.time()
        out = self.ar.qualify_all(names, log=self.log)
        self.assertLess(time.time() - t0, 6 * 0.4)            # not one after another
        self.assertEqual(peak[0], 6)
        self.assertEqual([r["app"] for r in out], names)       # library order, one result each
        self.assertEqual({r["handoff_state"] for r in out}, {"QUALIFIED"})
        rep = self.ar.LAST_REPORT
        self.assertEqual(rep["concurrency"]["mode"], "parallel")
        self.assertEqual(rep["concurrency"]["peak"], 6)
        self.assertEqual(rep["totals"]["QUALIFIED"], 6)
        self.assertTrue((self.ar.RUNNER / "RUN-REPORT.json").is_file())
        self.assertTrue((self.ar.RUNNER / "runs" / rep["fleet_run_id"] / "report.md").is_file())
        # Each job has its own identity; none shared.
        ids = [r["verification"]["run_id"] for r in out]
        cids = [r["verification"]["correlation_id"] for r in out]
        self.assertEqual(len(set(ids)), 6); self.assertEqual(len(set(cids)), 6)
        for r in out:
            self.assertIn(r["app"], r["verification"]["run_id"])
            self.assertEqual(r["verification"]["fleet_run_id"], rep["fleet_run_id"])
            self.assertTrue(r["verification"]["deployment_revision"].startswith(("code:", "release:")))
            self.assertTrue(r["verification"]["app_revision"].startswith("app:"))
            self.assertEqual(r["levels"], {"tcp_reachable": True, "http_response": True, "healthy": True,
                                           "qualified": True, "verified": True})

    def test_one_app_crashing_is_only_that_apps_result(self):
        names = self.apps("good1", "boom", "good2")
        def fake(app, log=print, **kw):
            if app == "boom":
                raise RuntimeError("kaboom in the runner")
            time.sleep(0.1)
            return self.passing(app)
        self.ar.qualify_app = fake
        out = {r["app"]: r for r in self.ar.qualify_all(names, log=self.log)}
        self.assertEqual(out["boom"]["handoff_state"], "INCONCLUSIVE")
        self.assertEqual(out["boom"]["code"], "RUNNER_EXCEPTION")
        self.assertIn("kaboom in the runner", out["boom"]["runner"]["traceback"])
        self.assertEqual(out["good1"]["handoff_state"], "QUALIFIED")
        self.assertEqual(out["good2"]["handoff_state"], "QUALIFIED")
        saved = json.loads((self.ar.RESULTS / "boom.json").read_text())
        self.assertEqual(saved["code"], "RUNNER_EXCEPTION")

    def test_a_crash_outside_the_check_and_a_vanished_app_are_contained(self):
        names = self.apps("ok", "weird") + ["ghost"]          # ghost: not in the library at all
        self.ar.qualify_app = lambda app, log=print, **kw: self.passing(app) if app != "ghost" else (_ for _ in ()).throw(FileNotFoundError("ghost is not in the library"))
        real = self.ar._app_revision_of
        def rev(a):
            if a == "weird":
                raise ValueError("identity code blew up")
            return real(a)
        self.ar._app_revision_of = rev
        out = {r["app"]: r for r in self.ar.qualify_all(names, log=self.log)}
        self.assertEqual(out["ok"]["handoff_state"], "QUALIFIED")
        self.assertEqual(out["weird"]["code"], "WORKER_EXCEPTION")
        self.assertEqual(out["weird"]["handoff_state"], "INCONCLUSIVE")
        self.assertEqual(out["ghost"]["code"], "RUNNER_EXCEPTION")   # built without its (missing) folder
        self.assertEqual(out["ghost"]["handoff_state"], "INCONCLUSIVE")
        self.assertEqual(self.ar.LAST_REPORT["apps_reported"], 3)

    def test_revision_change_mid_check_is_discarded_and_rechecked(self):
        names = self.apps("steady", "moving")
        calls = {"moving": 0}
        def fake(app, log=print, **kw):
            if app == "moving":
                calls["moving"] += 1
                if calls["moving"] == 1:   # a re-import lands while the first check runs
                    (self.lib / "moving" / "README.md").write_text("v2\n")
                    _git(self.lib / "moving", "commit", "-qam", "v2")
            return self.passing(app)
        self.ar.qualify_app = fake
        out = {r["app"]: r for r in self.ar.qualify_all(names, log=self.log)}
        m = out["moving"]
        self.assertEqual(calls["moving"], 2)
        self.assertEqual(m["handoff_state"], "QUALIFIED")
        self.assertEqual(m["verification"]["attempt"], 2)
        self.assertEqual(len(m["verification"]["invalidated_attempts"]), 1)
        inv = m["verification"]["invalidated_attempts"][0]
        self.assertNotEqual(inv["app_revision"], inv["app_revision_after"])
        self.assertEqual(m["verification"]["app_revision"], m["verification"]["app_revision_after"])
        self.assertNotEqual(inv["correlation_id"], m["verification"]["correlation_id"])
        self.assertIn("REVISION_CHANGED_RERUN", [e["kind"] for e in self.ar.LAST_REPORT["resource_events"]])
        self.assertEqual(out["steady"]["verification"]["attempt"], 1)

    def test_revision_that_keeps_changing_is_unverified_never_qualified(self):
        names = self.apps("churn")
        n = [0]
        def fake(app, log=print, **kw):
            n[0] += 1
            (self.lib / "churn" / ".ui-capability" / "skin.css").write_text(f"body{{order:{n[0]}}}\n")
            return self.passing(app)
        self.ar.qualify_app = fake
        [r] = self.ar.qualify_all(names, log=self.log)
        self.assertEqual(n[0], 1 + self.ar.REVISION_RERUNS)
        self.assertEqual(r["handoff_state"], "UNVERIFIED")
        self.assertEqual(r["verdict"], "UNVERIFIED")
        self.assertEqual(r["code"], "DEPLOYMENT_CHANGED_DURING_CHECK")
        self.assertEqual(r["stages"]["6 CLEAN"]["status"], "FAIL")
        self.assertNotIn("evidence", r["stages"]["6 CLEAN"])       # old-revision pictures are not the result's
        self.assertFalse(r["levels"]["verified"])
        self.assertIn("REVISION_UNSTABLE", [e["kind"] for e in self.ar.LAST_REPORT["resource_events"]])

    def test_a_hung_app_is_timeout_and_the_fleet_still_reports(self):
        names = self.apps("quick", "stuck")
        self.ar.APP_HARD_TIMEOUT = 1
        release = threading.Event()
        def fake(app, log=print, **kw):
            if app == "stuck":
                release.wait(10)
            return self.passing(app)
        self.ar.qualify_app = fake
        t0 = time.time()
        out = {r["app"]: r for r in self.ar.qualify_all(names, log=self.log)}
        self.assertLess(time.time() - t0, 5)
        self.assertEqual(out["stuck"]["handoff_state"], "TIMEOUT")
        self.assertEqual(out["stuck"]["code"], "WORKER_TIMEOUT")
        self.assertEqual(out["quick"]["handoff_state"], "QUALIFIED")
        self.assertIn("stuck", self.ar.LAST_REPORT["timeouts"])
        self.assertIn("WORKER_TIMEOUT", [e["kind"] for e in self.ar.LAST_REPORT["resource_events"]])
        # The hung job finishing later never overwrites the recorded result.
        release.set()
        late_dir = self.ar.RESULTS / "late"
        for _ in range(100):
            if late_dir.is_dir() and list(late_dir.glob("stuck.*.json")):
                break
            time.sleep(0.05)
        self.assertTrue(list(late_dir.glob("stuck.*.json")))
        self.assertEqual(json.loads((self.ar.RESULTS / "stuck.json").read_text())["code"], "WORKER_TIMEOUT")

    def test_one_at_a_time_is_never_silent(self):
        names = self.apps("s1", "s2", "s3")
        self.ar.qualify_app = lambda app, log=print, **kw: self.passing(app)
        self.ar.qualify_all(names, log=self.log, parallel=1)
        c = self.ar.LAST_REPORT["concurrency"]
        self.assertEqual((c["mode"], c["cap"], c["peak"]), ("sequential", 1, 1))
        self.assertIn("parallel=1", c["reason"])
        self.assertTrue(any("ONE AT A TIME" in l for l in self.logs))
        self.ar.qualify_all(names, log=self.log, parallel=2)
        self.assertEqual(self.ar.LAST_REPORT["concurrency"]["mode"], "capped")

    def test_machine_room_waits_are_reported(self):
        names = self.apps("r1", "r2", "r3")
        self.ar.START_FREE_DISK_GB = 10.0
        disk = {"gb": 1.0}
        self.ar._free_disk_gb = lambda: disk["gb"]
        def fake(app, log=print, **kw):
            time.sleep(0.3); disk["gb"] = 50.0   # room appears once the first app has run a while
            return self.passing(app)
        self.ar.qualify_app = fake
        out = self.ar.qualify_all(names, log=self.log)
        self.assertEqual({r["handoff_state"] for r in out}, {"QUALIFIED"})
        rep = self.ar.LAST_REPORT
        kinds = [e["kind"] for e in rep["resource_events"]]
        self.assertIn("WAIT_FOR_ROOM", kinds)
        self.assertIn("DISK_LOW_PRUNE", kinds)
        self.assertGreaterEqual(rep["concurrency"]["gate_waits"], 1)

    def test_starved_apps_get_a_recorded_sequential_second_pass(self):
        names = self.apps("big", "small")
        n = {"big": 0}
        def fake(app, log=print, **kw):
            if app == "big":
                n["big"] += 1
                if n["big"] == 1:
                    r = self.w.fail("big", {"1 INSTALLED": self.w.stage()}, "2 APP_UP", "RUNNER_EXHAUSTED", "No space left on device")
                    r["runner"] = {"started": False, "attempts": [{"rule": "disk.full"}]}
                    return r
            return self.passing(app)
        self.ar.qualify_app = fake
        out = {r["app"]: r for r in self.ar.qualify_all(names, log=self.log)}
        self.assertEqual(out["big"]["handoff_state"], "QUALIFIED")
        self.assertTrue(out["big"]["runner"]["second_pass"])
        self.assertTrue(json.loads((self.ar.RESULTS / "big.json").read_text())["runner"]["second_pass"])
        self.assertEqual(self.ar.LAST_REPORT["concurrency"]["second_pass"], ["big"])
        self.assertIn("SEQUENTIAL_SECOND_PASS", [e["kind"] for e in self.ar.LAST_REPORT["resource_events"]])

    def test_colliding_identities_are_reported_not_merged(self):
        make_app(self.lib, "My_App")
        self.ar.qualify_app = lambda app, log=print, **kw: self.passing(app)
        out = self.ar.qualify_all(["My_App", "my-app", "My_App"], log=self.log)
        self.assertEqual(len(out), 1)
        rows = self.ar.LAST_REPORT["apps"]
        self.assertEqual([(x["app"], x["state"]) for x in rows], [("my-app", "QUALIFIED"), ("my-app", "NOT_STARTED")])
        self.assertEqual(rows[1]["code"], "IDENTITY_COLLISION")
        self.assertTrue(any("IDENTITY COLLISION" in l for l in self.logs))

    def test_failed_start_evidence_rows_name_their_own_job(self):
        names = self.apps("e1", "e2", "e3", "e4")
        def fake(app, log=print, **kw):
            for i in range(25):
                self.ar._record_evidence(app, {"why": "compose"}, {"rule": "app.env_required", "fix": "set_env"},
                                         f"{app} error line {i}: FOO is required")
                time.sleep(0.001)
            return self.passing(app)
        self.ar.qualify_app = fake
        out = {r["app"]: r for r in self.ar.qualify_all(names, log=self.log)}
        rows = [json.loads(l) for l in (self.ar.RUNNER / "evidence.jsonl").read_text().splitlines()]
        self.assertEqual(len(rows), 100)
        for row in rows:
            v = out[row["app"]]["verification"]
            self.assertEqual(row["run_id"], v["run_id"])
            self.assertEqual(row["correlation_id"], v["correlation_id"])
            self.assertEqual(row["deployment_revision"], v["deployment_revision"])
            self.assertEqual(row["app_revision"], v["app_revision"])
            self.assertIn(row["app"], row["raw_tail"])
            for k in ("ts", "stage", "attempt", "source", "observation", "result"):
                self.assertIn(k, row)
        # Outside any job, a row carries no identity at all rather than a wrong one.
        self.ar._record_evidence("e1", {"why": "x"}, {"rule": "r"}, "later")
        last = json.loads((self.ar.RUNNER / "evidence.jsonl").read_text().splitlines()[-1])
        self.assertNotIn("run_id", last)

    def test_report_totals_and_classes_add_up(self):
        names = self.apps("p1", "p2", "skin", "nostart")
        def fake(app, log=print, **kw):
            if app == "skin":
                r = self.w.fail("skin", {k: self.w.stage() for k in ("1 INSTALLED", "2 APP_UP", "3 PROXY_UP")}, "4 SKIN", "NO_LINK")
                r["runner"] = {"started": True}
                return r
            if app == "nostart":
                r = self.w.fail("nostart", {"1 INSTALLED": self.w.stage()}, "2 APP_UP", "RUNNER_EXHAUSTED", "no way worked")
                r["runner"] = {"started": False, "attempts": [{"rule": "build.failed"}]}
                return r
            return self.passing(app)
        self.ar.qualify_app = fake
        self.ar.qualify_all(names, log=self.log)
        rep = json.loads((self.ar.RUNNER / "RUN-REPORT.json").read_text())
        self.assertEqual(sum(rep["totals"].values()), rep["apps_reported"])
        self.assertEqual((rep["totals"]["QUALIFIED"], rep["totals"]["FAILED"], rep["totals"]["START_FAILED"]), (2, 1, 1))
        classes = {(c["state"], c["stage"], c["code"]): c["apps"] for c in rep["failures_by_class"]}
        self.assertEqual(classes[("FAILED", "4 SKIN", "NO_LINK")], ["skin"])
        self.assertEqual(classes[("START_FAILED", "2 APP_UP", "RUNNER_EXHAUSTED")], ["nostart"])
        self.assertTrue(rep["deployment_revision"]["stable"])
        md = (self.ar.RUNNER / "RUN-REPORT.md").read_text()
        self.assertIn("| skin | FAILED |", md)
        self.assertIn("Concurrency: mode parallel", md)

    def test_main_all_finishes_without_a_name_error(self):
        self.apps("m1", "m2")
        self.ar.qualify_app = lambda app, log=print, **kw: self.passing(app)
        rc = self.ar.main(["app_runner.py", "all", "--fresh"])
        self.assertIn(rc, (0, 1))
        self.assertTrue((self.ar.RUNNER / "CHECKLIST.md").is_file())


# ------------------------------------------------------------------------------------------ the real qualify_app
class RealQualifyApp(_Root):
    """qualify_app itself runs; only Docker (run_app/start_proxy/down) and the watcher's check are stood in."""
    def test_identity_travels_in_memory_never_in_the_target_file(self):
        make_app(self.lib, "svc", qualification="service")
        class H(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(200); self.send_header("Content-Type", "text/html"); self.end_headers(); self.wfile.write(b"<html></html>")
            def log_message(self, *a): pass
        srv = http.server.HTTPServer(("127.0.0.1", 0), H)
        th = threading.Thread(target=srv.serve_forever, daemon=True); th.start()
        seen = {}
        try:
            url = f"http://127.0.0.1:{srv.server_port}"
            self.ar.run_app = lambda app, log=print: {"ok": True, "url": url, "part": {"why": "test part", "kind": "image"}, "attempts": []}
            self.ar.start_proxy = lambda app, u: (True, "", {"app": "svc", "target_url": u, "proxy_url": u})
            class FakeW:
                stage = staticmethod(self.w.stage); fail = staticmethod(self.w.fail); result = staticmethod(self.w.result)
                def check(inner, t):
                    seen["t"] = t
                    seen["file"] = json.loads((self.ar.TARGETS / "svc.json").read_text())
                    return self.passing("svc")
            self.ar._watcher = lambda: FakeW()
            r = self.ar._qualify_one("svc", self.log)
        finally:
            srv.shutdown(); srv.server_close(); th.join(3)
        self.assertNotIn("_identity", seen["file"])
        for k in ("_run_id", "_deployment_revision", "_attempt"):
            self.assertNotIn(k, seen["file"])
        ident = seen["t"]["_identity"]
        self.assertEqual(ident["run_id"], r["verification"]["run_id"])
        self.assertEqual(ident["correlation_id"], r["verification"]["correlation_id"])
        self.assertEqual(ident["source"], "app_runner.qualify_app")
        self.assertEqual(r["observations"]["app_http"]["http_status"], 200)
        self.assertTrue(r["observations"]["app_http"]["tcp_connected"])
        self.assertEqual(r["handoff_state"], "QUALIFIED")
        self.assertEqual(json.loads((self.ar.RESULTS / "svc.json").read_text())["handoff_state"], "QUALIFIED")


# ------------------------------------------------------------------------------------------ stage-6 evidence
class _Page:
    def __init__(self, n): self.url = f"http://p/{n}"; self.n = n
    def screenshot(self, path, full_page=True): Path(path).write_bytes(b"PNG" + str(self.n).encode())
    def content(self): return f"<html>{self.n}</html>"
    def title(self): return f"t{self.n}"


class Stage6Evidence(_Root):
    def test_each_check_has_its_own_folder_with_its_identity(self):
        m1 = {"run_id": "run-1-app-aa", "attempt": 1, "correlation_id": "c-1", "deployment_revision": "code:1",
              "app_revision": "app:1", "fleet_run_id": "fleet-1", "source": "app_runner.qualify_app"}
        m2 = {**m1, "attempt": 2, "correlation_id": "c-2", "app_revision": "app:2"}
        e1 = self.w._capture("app", _Page(1), [], [], [], metadata=m1)
        e2 = self.w._capture("app", _Page(2), [], [], [], metadata=m2)
        e3 = self.w._capture("app", _Page(3), [], [], [], metadata=None)       # a --loop check
        e4 = self.w._capture("app", _Page(4), [], [], [], metadata=m1)       # same job capturing again
        dirs = {e["dir"] for e in (e1, e2, e3, e4)}
        self.assertEqual(len(dirs), 4)
        self.assertTrue(Path(e3["dir"]).name.startswith("watch-"))
        self.assertEqual((Path(e1["dir"]) / "screenshot.png").read_bytes(), b"PNG1")
        b1 = json.loads((Path(e1["dir"]) / "browser.json").read_text())
        b2 = json.loads((Path(e2["dir"]) / "browser.json").read_text())
        self.assertEqual((b1["correlation_id"], b1["app_revision"], b1["attempt"], b1["stage"], b1["app"]), ("c-1", "app:1", 1, "6 CLEAN", "app"))
        self.assertEqual((b2["correlation_id"], b2["app_revision"], b2["attempt"]), ("c-2", "app:2", 2))
        b3 = json.loads((Path(e3["dir"]) / "browser.json").read_text())
        self.assertNotIn("run_id", b3)
        self.assertIn("ts", b1)

    def test_old_check_folders_are_pruned_oldest_first(self):
        self.w.EVIDENCE_KEEP = 2
        es = []
        for i in range(4):
            es.append(self.w._capture("app", _Page(i), [], [], [], metadata={"run_id": f"run-{i}-app-x", "attempt": 1}))
            time.sleep(0.02)
        left = sorted(p.name for p in (self.w.EVIDENCE_DIR / "app").iterdir())
        self.assertEqual(left, [Path(es[2]["dir"]).name, Path(es[3]["dir"]).name])

    def test_loop_checks_never_prune_the_evidence_a_result_points_at(self):
        self.w.EVIDENCE_KEEP = 2
        mine = self.w._capture("app", _Page(0), [], [], [], metadata={"run_id": "run-1-app-x", "attempt": 1})
        res = {"app": "app", "stages": {"6 CLEAN": {"status": "OK", "evidence": mine}}}
        (self.ar.RESULTS / "app.json").write_text(json.dumps(res))
        older = self.w._capture("app", _Page(1), [], [], [], metadata={"run_id": "run-0-app-y", "attempt": 1})
        for i in range(6):                       # hours of --loop sweeps with KEEP_RUNNING=true
            self.w._capture("app", _Page(10 + i), [], [], [], metadata=None)
            time.sleep(0.01)
        names = {p.name for p in (self.w.EVIDENCE_DIR / "app").iterdir()}
        self.assertIn(Path(mine["dir"]).name, names)      # the recorded result's pictures
        self.assertIn(Path(older["dir"]).name, names)     # runner folders are not pushed out by loop folders
        self.assertEqual(len([n for n in names if n.startswith("watch-")]), 2)


# ------------------------------------------------------------------------------------------ gateway evidence links
class GatewayEvidence(unittest.TestCase):
    """The real gateway.py, as a process: a run folder is served, the old flat URL never falls through to a
    newer run (another revision's picture), and nothing outside the evidence tree is reachable."""
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        s = socket.socket(); s.bind(("127.0.0.1", 0)); self.port = s.getsockname()[1]; s.close()
        env = {**os.environ, "APP_BUILDER_ROOT": str(self.root), "APP_BUILDER_AUTH_DISABLED": "1",
               "APP_BUILDER_HOST": "127.0.0.1", "APP_BUILDER_PORT": str(self.port), "APP_BUILDER_SESSION_SECRET": "x"}
        self.proc = subprocess.Popen([sys.executable, "gateway.py"], cwd=DEP, env=env,
                                     stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        for _ in range(100):
            try:
                socket.create_connection(("127.0.0.1", self.port), 0.2).close(); break
            except OSError:
                time.sleep(0.05)

    def tearDown(self):
        self.proc.terminate(); self.proc.wait(5); self.proc.stderr.close()
        shutil.rmtree(self.root, ignore_errors=True)

    def get(self, path):
        import urllib.request, urllib.error
        try:
            with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(f"http://127.0.0.1:{self.port}{path}", timeout=5) as r:
                return r.status, r.read()
        except urllib.error.HTTPError as e:
            return e.code, b""

    def test_routes(self):
        ev = self.root / "state/runner/evidence/memos"
        (ev / "run-1-memos-ab-a1").mkdir(parents=True)
        (ev / "run-1-memos-ab-a1" / "browser.json").write_text('{"run":"new"}')
        self.assertEqual(self.get("/evidence/memos/run-1-memos-ab-a1/browser.json"), (200, b'{"run":"new"}'))
        self.assertEqual(self.get("/evidence/memos/browser.json")[0], 404)        # no fall-through to a run
        (ev / "browser.json").write_text('{"run":"legacy"}')
        self.assertEqual(self.get("/evidence/memos/browser.json"), (200, b'{"run":"legacy"}'))
        (self.root / "state/secret.json").write_text("nope")
        for bad in ("/evidence/memos/../../secret.json", "/evidence/memos/..%2f..%2fsecret.json/browser.json",
                    "/evidence/memos/run-1/../browser.json", "/evidence/memos/RUN-1-MEMOS-AB-A1/browser.json"):
            self.assertNotEqual(self.get(bad)[1], b"nope", bad)
            self.assertNotEqual(self.get(bad)[1], b'{"run":"new"}', bad)
        self.assertIsNone(self.proc.poll(), "gateway died")

    def test_links_name_the_run(self):
        import http.server as hs
        real = hs.ThreadingHTTPServer
        class NoServe:
            def __init__(self, *a, **k): pass
            def serve_forever(self): pass
        hs.ThreadingHTTPServer = NoServe
        os.environ["APP_BUILDER_ROOT"] = str(self.root); os.environ["APP_BUILDER_SESSION_SECRET"] = "x"
        os.environ["APP_BUILDER_AUTH_DISABLED"] = "1"
        try:
            sys.modules.pop("gateway", None)
            gw = importlib.import_module("gateway")
        finally:
            hs.ThreadingHTTPServer = real
            sys.modules.pop("gateway", None)
            os.environ.pop("APP_BUILDER_AUTH_DISABLED", None)
        out = gw.discovered_html({"qualification": [
            {"app": "memos", "evidence": {"dir": "/x/memos/run-1-memos-ab-a1", "run": "run-1-memos-ab-a1", "browser": "b", "screenshot": "s"}},
            {"app": "old", "evidence": {"dir": "/x/old", "browser": "b"}},
            {"app": "evil", "evidence": {"run": "../../etc", "browser": "b"}}]})
        self.assertIn("/evidence/memos/run-1-memos-ab-a1/browser.json", out)
        self.assertIn("/evidence/memos/run-1-memos-ab-a1/screenshot.png", out)
        self.assertIn("/evidence/old/browser.json", out)
        self.assertNotIn("../", out)


# ------------------------------------------------------------------------------------------ the hand-off gate
class PipelineGate(_Root):
    def test_only_barrier_passed_current_revision_results_are_qualified(self):
        pl = importlib.import_module("pipeline")
        import rule_lifecycle
        live = resilience.deployment_revision(self.root)
        def res(app, **kw):
            r = self.passing(app)
            r["verification"] = {"status": "CURRENT", "deployment_revision": live, "run_id": "run-" + app}
            r.update(kw); r["handoff_state"] = handoff_state(r)
            return r
        good = res("good")
        unver = res("unver", verdict="UNVERIFIED", broken_at="6 CLEAN", code="DEPLOYMENT_CHANGED_DURING_CHECK",
                    verification={"status": "UNVERIFIED", "deployment_revision": live})
        old = res("old", verification={"status": "CURRENT", "deployment_revision": "code:someotherrevision"})
        nobarrier = self.passing("nobarrier")                     # a pre-v121.2 result: no verification at all
        results = [good, unver, old, nobarrier]
        self.ar.library_apps = lambda: [r["app"] for r in results]
        self.ar.qualify_all = lambda todo, log=print, parallel=None: results
        rule_lifecycle.after_full_run = lambda *a, **k: {"promoted": [], "suspects": []}
        st, apps, why, _ = pl.qualify()
        q = {a["app"]: a["qualified"] for a in apps}
        self.assertEqual(q, {"good": True, "unver": False, "old": False, "nobarrier": False})
        self.assertIn("old: UNVERIFIED", why)
        self.assertIn("nobarrier: INCONCLUSIVE", why)
        self.assertEqual(st, "PARTIALLY_QUALIFIED")


if __name__ == "__main__":
    unittest.main()
