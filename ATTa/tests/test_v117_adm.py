"""v117 (checklists C, D, E, F, G): ADM's state machine, exact rollback, stoppable deploys, one deploy at a time.

    cd ATTa && python3 -m unittest tests.test_v117_adm -v

Unit tests for each part, then whole deploys with REAL processes: a fake bundle whose `run` follows exactly the
contract the real bootstrap.sh follows (stage off to the side -> built -> snapshot -> switch -> restart a real
gateway -> identity check -> verified, or failed + exact restore), a real gateway restarted by a stand-in for
systemd, and real timeouts, kills and races. The real bootstrap.sh is exercised end to end on systemd by
tests/staging/e2e_container.sh."""
import json, os, shutil, signal, stat, subprocess, sys, threading, time, unittest, zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from atta_testlib import BUNDLE, DEP, free_port, tmpdir, wait_until, write_env  # noqa: E402
from adm import (config, journal, queue, deployment, releases, manager, activation, authz, health,  # noqa: E402
                 staging, lock as adm_lock, proc)
import atta_identity  # noqa: E402

LINUX = sys.platform.startswith("linux")
DEPLOYSTATE = str(DEP / "deployd" / "deploystate.py")


def configure(tmp):
    """Point every ADM path at a throwaway folder (ADM's config is read at import, possibly by another test file)."""
    tmp = Path(tmp)
    config.ROOT = tmp / "root"
    config.APP = tmp / "app"
    config.ADM = tmp / "adm"
    for name, rel in (("INCOMING", "incoming"), ("REQUESTS", "requests"), ("QUEUE", "state/queue"),
                      ("RUNNING", "state/running"), ("JOURNAL", "state/journal"), ("STAGING", "staging"),
                      ("RELEASES", "releases"), ("BACKUPS", "backups"), ("LOGS", "logs"), ("CURRENT", "current"),
                      ("PREVIOUS", "previous"), ("LOCK", "state/deployd.lock"), ("KNOWN_GOOD", "state/known_good.json")):
        setattr(config, name, config.ADM / rel)
    config.CODE_RELEASES = tmp / "releases"
    config.ENV_FILE = tmp / ".env"
    config.PROXY_FILE = config.ROOT / "state" / "proxy.json"
    config.PIPELINE_LOCK = config.ROOT / "state" / "pipeline.lock"
    config.DIRS = (config.INCOMING, config.QUEUE, config.RUNNING, config.JOURNAL, config.STAGING, config.RELEASES,
                   config.BACKUPS, config.LOGS)
    config.PRIVATE_DIRS = (config.INCOMING, config.QUEUE, config.RUNNING)
    config.TRUSTED_UID = os.geteuid()
    config.SERVICES = []
    config.REQUIRE_PROXY = False
    config.BROWSER_CHECK = False
    config.RUN_TESTS = False
    config.HEALTH_RETRIES, config.HEALTH_RETRY_SECONDS = 20, 0.5
    config.KILL_GRACE = 3
    config.POLL_SECONDS = 1
    config.KEEP_RELEASES = 3
    authz.USERS_FILE = config.ROOT / "state" / "users.json"
    for d in (config.ROOT, config.CODE_RELEASES):
        d.mkdir(parents=True, exist_ok=True)
    config.ensure_dirs()
    # the environment the fake bundle's `run` (and deploystate/releases CLIs) sees
    os.environ.update(APP_BUILDER_ROOT=str(config.ROOT), ATTA_ADM_ROOT=str(config.ADM),
                      ATTA_T_APP=str(config.APP), ATTA_T_RELS=str(config.CODE_RELEASES),
                      ATTA_T_ENV=str(config.ENV_FILE), ATTA_T_CTL=str(tmp / "ctl"))


class Unit(unittest.TestCase):
    def setUp(self):
        self.t = tmpdir("atta-adm-unit-")
        configure(self.t)


class StateMachine(Unit):
    def test_happy_path_and_verdict(self):
        deployment.create("j1", kind="adm")
        for s in ("validating", "building", "built", "health_checking", "verified", "live"):
            deployment.transition("j1", s)
        d = deployment.get("j1")
        self.assertEqual((d["state"], d["verdict"]), ("live", "DEPLOYED"))
        self.assertTrue(d["completed_at"])
        self.assertEqual([h["state"] for h in d["state_history"]],
                         ["created", "validating", "building", "built", "health_checking", "verified", "live"])

    def test_failed_can_never_become_live(self):
        for fail_at in ("created", "validating", "building", "built", "health_checking", "verified"):
            job = f"j-{fail_at}"
            deployment.create(job, kind="adm")
            path = ["validating", "building", "built", "health_checking", "verified"]
            for s in path[:path.index(fail_at) + 1] if fail_at != "created" else []:
                deployment.transition(job, s)
            for bad in ("failed", "timed_out", "interrupted"):
                pass
            deployment.transition(job, "failed", reason="x")
            for target in ("live", "verified", "health_checking", "built", "building", "validating"):
                with self.subTest(fail_at=fail_at, target=target), self.assertRaises(deployment.IllegalTransition):
                    deployment.transition(job, target)

    def test_live_only_from_verified(self):
        deployment.create("j2", kind="adm")
        for s in ("validating", "building", "built", "health_checking"):
            deployment.transition("j2", s)
        with self.assertRaises(deployment.IllegalTransition):
            deployment.transition("j2", "live")

    def test_rolled_back_needs_a_verified_rollback(self):
        deployment.create("j3", kind="adm")
        deployment.transition("j3", "validating")
        deployment.transition("j3", "timed_out", reason="slow")
        with self.assertRaises(deployment.IllegalTransition):
            deployment.transition("j3", "rolled_back")
        deployment.set_rollback("j3", deployment.SUCCEEDED, target="/x")
        deployment.transition("j3", "rolled_back")
        d = deployment.get("j3")
        self.assertEqual((d["verdict"], d["failure_reason"]), ("ROLLED_BACK", "slow"))

    def test_verdicts_of_unsettled_and_failed_rollbacks(self):
        deployment.create("j4", kind="adm")
        deployment.transition("j4", "interrupted", reason="killed")
        self.assertFalse(deployment.is_final(deployment.get("j4")))
        self.assertIn("j4", [d["job_id"] for d in deployment.unfinished()])
        deployment.set_rollback("j4", deployment.ROLLBACK_FAILED, reason="no target")
        self.assertEqual(deployment.get("j4")["verdict"], "ROLLBACK_FAILED")
        self.assertNotIn("j4", [d["job_id"] for d in deployment.unfinished()])

    def test_create_twice_refused_and_ids_checked(self):
        deployment.create("j5", kind="adm")
        with self.assertRaises(deployment.IllegalTransition):
            deployment.create("j5", kind="adm")
        with self.assertRaises(ValueError):
            journal.path("../../etc/passwd")

    def test_cli_used_by_bootstrap(self):
        env = {**os.environ, "ATTA_DEPLOYMENT_ID": ""}
        r = subprocess.run([sys.executable, DEPLOYSTATE, "begin"], env=env, capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        job = r.stdout.strip()
        self.assertTrue(job.endswith("-manual"))
        env["ATTA_DEPLOYMENT_ID"] = job
        run = lambda *a: subprocess.run([sys.executable, DEPLOYSTATE, *a], env=env, capture_output=True, text=True)
        self.assertEqual(run("state").stdout.strip(), "validating")
        self.assertEqual(run("to", "live").returncode, 1)                    # refused
        self.assertEqual(run("to", "building").returncode, 0)
        self.assertEqual(run("set", "attempted", '{"release": "/r/x"}').returncode, 0)
        self.assertEqual(deployment.get(job)["attempted"]["release"], "/r/x")


class Registry(Unit):
    def rel(self, name, version="v1"):
        d = config.CODE_RELEASES / name
        d.mkdir(parents=True)
        (d / "gateway.py").write_text(f"# {name}\n")
        (d / "release.json").write_text(json.dumps({"version": version}))
        return d

    def test_verify_only_the_live_release(self):
        a, b = self.rel("a"), self.rel("b")
        releases.switch(config.APP, a)
        with self.assertRaisesRegex(releases.ReleaseError, "not the live release"):
            releases.mark_verified(config.APP, b, "j")
        releases.mark_verified(config.APP, a, "j")
        self.assertTrue(releases.is_verified(a))

    def test_promote_requires_verified_by_this_deployment_and_live(self):
        a, b = self.rel("a"), self.rel("b")
        releases.switch(config.APP, a)
        with self.assertRaisesRegex(releases.ReleaseError, "not verified"):
            releases.promote(config.CODE_RELEASES, config.APP, a, "j1")
        releases.mark_verified(config.APP, a, "j1")
        with self.assertRaisesRegex(releases.ReleaseError, "verified by deployment j1"):
            releases.promote(config.CODE_RELEASES, config.APP, a, "j2")
        releases.promote(config.CODE_RELEASES, config.APP, a, "j1")
        releases.switch(config.APP, b)
        releases.mark_verified(config.APP, b, "j2")
        releases.switch(config.APP, a)                       # b verified but no longer live
        with self.assertRaisesRegex(releases.ReleaseError, "not live"):
            releases.promote(config.CODE_RELEASES, config.APP, b, "j2")

    def test_failed_release_never_verified_or_known_good(self):
        a = self.rel("a")
        releases.switch(config.APP, a)
        releases.mark_failed(a, "j", "broke")
        with self.assertRaisesRegex(releases.ReleaseError, "marked failed"):
            releases.mark_verified(config.APP, a, "j")
        (a / releases.VERIFIED).write_text(json.dumps({"deployment_id": "j"}))   # even a forged marker
        self.assertFalse(releases.is_verified(a))
        with self.assertRaises(releases.ReleaseError):
            releases.promote(config.CODE_RELEASES, config.APP, a, "j")

    def test_known_good_and_previous_chain(self):
        a, b, c = self.rel("a", "v1"), self.rel("b", "v2"), self.rel("c", "v3")
        for r, j in ((a, "ja"), (b, "jb"), (c, "jc")):
            releases.switch(config.APP, r)
            releases.mark_verified(config.APP, r, j)
            releases.promote(config.CODE_RELEASES, config.APP, r, j)
        self.assertEqual(Path(releases.known_good(config.CODE_RELEASES)["release"]), c.resolve())
        self.assertEqual(Path(releases.previous_known_good(config.CODE_RELEASES)["release"]), b.resolve())

    def test_prune_keeps_live_known_good_previous_and_removes_unverified(self):
        rs = [self.rel(f"r{i}", f"v{i}") for i in range(6)]
        for i, r in enumerate(rs[:5]):
            os.utime(r, (1000 + i, 1000 + i))
            releases.switch(config.APP, r)
            releases.mark_verified(config.APP, r, f"j{i}")
            releases.promote(config.CODE_RELEASES, config.APP, r, f"j{i}")
        releases.switch(config.APP, rs[4])
        broken = rs[5]                                           # never verified: a failed deploy's leftover
        removed = releases.prune(config.CODE_RELEASES, config.APP, keep=1)
        self.assertIn(str(broken), removed)
        left = {p.name for p in config.CODE_RELEASES.iterdir() if p.is_dir()}
        self.assertTrue({"r4", "r3"} <= left, left)              # live/known-good and previous-known-good
        self.assertNotIn("r0", left)

    def test_adopt_only_what_is_live(self):
        a = self.rel("a")
        self.assertIsNone(releases.adopt(config.CODE_RELEASES, config.APP, "j"))
        releases.switch(config.APP, a)
        rec = releases.adopt(config.CODE_RELEASES, config.APP, "j")
        self.assertTrue(rec["adopted"])
        self.assertEqual(Path(rec["release"]), a.resolve())

    def test_tree_hash_is_content_not_location(self):
        a = self.rel("a")
        b = config.CODE_RELEASES / "copy"
        shutil.copytree(a, b)
        (b / "__pycache__").mkdir()
        (b / "__pycache__" / "x.pyc").write_bytes(b"cache")
        self.assertEqual(releases.tree_sha256(a), releases.tree_sha256(b))
        (b / "gateway.py").write_text("changed")
        self.assertNotEqual(releases.tree_sha256(a), releases.tree_sha256(b))


class Lock(Unit):
    def test_busy_names_the_owner_and_nothing_starts(self):
        with adm_lock.DeployLock(config.LOCK, "job-A"):
            with self.assertRaises(adm_lock.Busy) as c:
                adm_lock.DeployLock(config.LOCK, "job-B").acquire()
            self.assertIn("job-A", str(c.exception))
            self.assertEqual(c.exception.owner["pid"], os.getpid())
            held, owner = adm_lock.holder(config.LOCK)
            self.assertTrue(held)
            self.assertTrue(adm_lock.owner_alive(owner))
        self.assertEqual(adm_lock.holder(config.LOCK), (False, None))   # owner record removed by its owner

    def test_stale_owner_record_is_recognised_not_trusted(self):
        rec = {"deployment_id": "dead-job", "pid": 999999999, "pid_start": 1, "hostname": os.uname().nodename,
               "acquired_at": "x"}
        adm_lock.owner_path(config.LOCK).write_text(json.dumps(rec))
        self.assertFalse(adm_lock.owner_alive(rec))
        with adm_lock.DeployLock(config.LOCK, "new-job") as lk:
            self.assertEqual(lk.stale_owner["deployment_id"], "dead-job")
            self.assertEqual(adm_lock.read_owner(config.LOCK)["deployment_id"], "new-job")

    def test_owner_record_only_removed_by_its_owner(self):
        lk = adm_lock.DeployLock(config.LOCK, "job-A").acquire()
        adm_lock.write_owner_for(config.LOCK, "someone-else", os.getpid())
        lk.release()
        self.assertEqual(adm_lock.read_owner(config.LOCK)["deployment_id"], "someone-else")

    def test_other_process_cannot_take_it(self):
        with adm_lock.DeployLock(config.LOCK, "job-A"):
            code = ("import sys; sys.path.insert(0, sys.argv[1]); from adm import lock\n"
                    "try:\n lock.DeployLock(sys.argv[2], 'job-B').acquire(); print('GOT')\n"
                    "except lock.Busy as e: print('BUSY', e)")
            r = subprocess.run([sys.executable, "-c", code, str(DEP / "deployd"), str(config.LOCK)],
                               capture_output=True, text=True, timeout=30)
            self.assertTrue(r.stdout.startswith("BUSY"), r.stdout + r.stderr)
            self.assertIn("job-A", r.stdout)


class Claim(Unit):
    def _zip(self):
        z = self.t / "b.zip"
        with zipfile.ZipFile(z, "w") as f:
            f.writestr("x", "y")
        return z

    def test_a_job_is_claimed_once(self):
        job = queue.enqueue(self._zip(), origin="local", requested_by="deployctl")
        meta = queue.pending()[0]
        results = []
        barrier = threading.Barrier(8)

        def worker():
            barrier.wait()
            results.append(queue.claim(meta))
        ts = [threading.Thread(target=worker) for _ in range(8)]
        [t.start() for t in ts]
        [t.join() for t in ts]
        self.assertEqual(sum(1 for r in results if r is not None), 1)
        self.assertEqual(queue.pending(), [])
        self.assertEqual([m["job_id"] for m in queue.running()], [job])

    def test_deployctl_jobs_never_enter_the_shared_queue(self):
        queue.enqueue(self._zip(), origin="local", requested_by="deployctl", claimed=True, job_id="20260101T000000Z-x")
        self.assertEqual(queue.pending(), [])
        self.assertIsNone(queue.claim_next())
        self.assertEqual([m["job_id"] for m in queue.running()], ["20260101T000000Z-x"])

    def test_claimed_job_still_authorised_where_it_is(self):
        queue.enqueue(self._zip(), origin="local", requested_by="deployctl")
        meta = queue.claim_next()
        self.assertEqual(authz.allowed(meta), (True, "local"))

    def test_job_ids_cannot_repeat(self):
        queue.enqueue(self._zip(), origin="local", requested_by="deployctl", job_id="20260101T000000Z-y")
        with self.assertRaises(ValueError):
            queue.enqueue(self._zip(), origin="local", requested_by="deployctl", job_id="20260101T000000Z-y")


# ----------------------------------------------------------------------------------------------------------------
# Whole deploys with real processes.

FAKE_RUN = r'''#!/usr/bin/env bash
# The bootstrap.sh CONTRACT, minus the system work: stage off to the side, built, snapshot, switch, restart,
# identity check through the port .env names, verified — or failed + exact restore. Mode comes from release.json.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
# As a bundle's `run` (04-deployment beside it) or as a code release's bootstrap.sh (it IS the code folder).
if [ -d "$HERE/04-deployment" ]; then DEP="$HERE/04-deployment"; else DEP="$HERE"; fi
MODE="$(python3 -c 'import json,sys;print(json.load(open(sys.argv[1])).get("test_mode","ok"))' "$HERE/release.json")"
VER="$(python3 -c 'import json,sys;print(json.load(open(sys.argv[1]))["version"])' "$HERE/release.json")"
DS(){ python3 "$DEP/deployd/deploystate.py" "$@"; }
REL(){ python3 "$DEP/deployd/adm/releases.py" "$@"; }
APP="$ATTA_T_APP"; RELS="$ATTA_T_RELS"; ENVF="$ATTA_T_ENV"; CTL="$ATTA_T_CTL"
ATTA_DEPLOYMENT_ID="$(DS begin)"; export ATTA_DEPLOYMENT_ID
[ "$(DS state)" = validating ] && DS to building
restart(){ local tok="t$RANDOM$RANDOM"; printf %s "$tok" >"$CTL/restart.req"
  for _ in $(seq 1 200); do [ "$(cat "$CTL/restart.ack" 2>/dev/null)" = "$tok" ] && return 0; sleep 0.05; done; return 1; }
NEW="$RELS/$VER-$(date +%s%N)"
mkdir -p "$NEW"; cp -a "$DEP/." "$NEW/"; cp "$HERE/release.json" "$NEW/release.json"
DS set attempted "{\"release\": \"$NEW\", \"version\": \"$VER\"}"
case "$MODE" in
  fail_prepare) REL failed "$NEW" "$ATTA_DEPLOYMENT_ID" "prepare failed"; DS to failed "prepare failed (packages)"; exit 1 ;;
  hang_prepare) sleep 600 ;;
  orphan_prepare) (setsid bash -c 'exec -a atta-orphan-test sleep 600' &); sleep 600 ;;
esac
DS to built
PREV="$(readlink -f "$APP" 2>/dev/null || true)"
TXN="$RELS/.txn/$ATTA_DEPLOYMENT_ID"; mkdir -p "$TXN"
cat >"$TXN/restore.sh" <<EOF
set -e
[ -n "$PREV" ] || exit 1
ln -sfn "$PREV" "$APP.restore" && mv -Tf "$APP.restore" "$APP"
tok="r\$RANDOM"; printf %s "\$tok" >"$CTL/restart.req"
for _ in \$(seq 1 200); do [ "\$(cat "$CTL/restart.ack" 2>/dev/null)" = "\$tok" ] && break; sleep 0.05; done
python3 "$PREV/atta_health.py" --env "$ENVF" --timeout 20 >/dev/null
EOF
DS set transaction "\"$TXN\""
DS to health_checking
ln -sfn "$NEW" "$APP.next" && mv -Tf "$APP.next" "$APP"
restart
[ "$MODE" = hang_after_switch ] && sleep 600
if [ "$MODE" != leave_process ] && python3 "$NEW/atta_health.py" --env "$ENVF" --expect-release "$(basename "$NEW")" --timeout 10; then
  REL verify "$APP" "$NEW" "$ATTA_DEPLOYMENT_ID" >/dev/null
  DS to verified
  exit 0
fi
if [ "$MODE" = leave_process ]; then
  REL verify "$APP" "$NEW" "$ATTA_DEPLOYMENT_ID" >/dev/null; DS to verified
  (exec -a atta-leftover-test sleep 600) &
  exit 0
fi
DS to failed "the new gateway did not pass its identity check"
if bash "$TXN/restore.sh"; then DS set local_restore '{"result": "verified"}'; fi
exit 1
'''


@unittest.skipUnless(LINUX, "ADM runs on Linux servers")
class Deploys(unittest.TestCase):
    """Deploy A, then B, then every kind of failure: each must leave B live, exactly."""

    @classmethod
    def setUpClass(cls):
        cls.t = tmpdir("atta-adm-deploy-")
        configure(cls.t)
        cls.port = free_port()
        cls.secret = "adm-test-secret-" + "x" * 40
        write_env(config.ENV_FILE, APP_BUILDER_ROOT=config.ROOT, APP_BUILDER_HOST="127.0.0.1",
                  APP_BUILDER_PORT=cls.port, APP_BUILDER_SESSION_SECRET=cls.secret, APP_BUILDER_TEST_ACCOUNTS=1)
        (config.ROOT / "front-door.html").write_text("<html><body>fd</body></html>")
        env = {**os.environ, "APP_BUILDER_ROOT": str(config.ROOT), "APP_BUILDER_TEST_ACCOUNTS": "1"}
        subprocess.run([sys.executable, str(DEP / "accounts.py"), "init"], env=env, check=True, capture_output=True)
        ctl = cls.t / "ctl"; ctl.mkdir()
        cls.sysd = subprocess.Popen([sys.executable, str(Path(__file__).parent / "fake_systemd.py"), str(ctl),
                                     str(config.APP), str(config.ENV_FILE)], start_new_session=True)
        wait_until(lambda: (ctl / "ready").exists(), 10)
        cls.versions = {}

    @classmethod
    def tearDownClass(cls):
        cls.sysd.terminate(); cls.sysd.wait(10)
        for pid in proc._all_pids():
            if "atta-orphan-test" in proc.cmdline(pid) or "atta-leftover-test" in proc.cmdline(pid):
                os.kill(pid, signal.SIGKILL)

    def bundle(self, version, mode="ok", broken_gateway=False):
        src = self.t / f"src-{version}-{mode}"
        root = src / "ATTa"
        shutil.copytree(DEP, root / "04-deployment", ignore=shutil.ignore_patterns("__pycache__"))
        if broken_gateway:
            g = root / "04-deployment" / "gateway.py"
            g.write_text("raise SystemExit('broken on purpose')\n" + g.read_text())
        for f in (root / "run", root / "04-deployment" / "bootstrap.sh"):   # never the real installer in a test
            f.write_text(FAKE_RUN)
            f.chmod(0o755)
        (root / "release.json").write_text(json.dumps({"version": version, "test_mode": mode,
                                                       "features": [atta_identity.FEATURE]}))
        z = self.t / f"{version}-{mode}.zip"
        with zipfile.ZipFile(z, "w") as f:
            for p in sorted(src.rglob("*")):
                if p.is_file():
                    f.write(p, p.relative_to(src))
        return z

    def deploy(self, z, timeout=None):
        if timeout:
            old, config.DEPLOY_TIMEOUT = config.DEPLOY_TIMEOUT, timeout
        try:
            job = queue.enqueue(z, origin="local", requested_by="deployctl", claimed=True)
            meta = json.loads((config.RUNNING / f"{job}.json").read_text())
            verdict = manager.process(meta)
        finally:
            if timeout:
                config.DEPLOY_TIMEOUT = old
        return job, verdict, deployment.get(job)

    def live(self):
        return releases.live(config.APP)

    def assertServing(self, release):
        ok, lines = __import__("atta_health").run_checks(config.ENV_FILE, expect_release=Path(release).name, timeout=15)
        self.assertTrue(ok, lines)

    def ensure_ab(self):
        if "B" in self.versions:
            return
        job, verdict, d = self.deploy(self.bundle("vA"))
        self.assertEqual(verdict, "DEPLOYED", Path(d["log"]).read_text()[-3000:])
        self.versions["A"] = self.live()
        job, verdict, d = self.deploy(self.bundle("vB"))
        self.assertEqual(verdict, "DEPLOYED", Path(d["log"]).read_text()[-3000:])
        self.versions["B"] = self.live()

    def assertBLiveExactly(self, d):
        B = self.versions["B"]
        self.assertEqual(self.live(), B, "the live release is not exactly the one live before")
        self.assertEqual(Path(releases.known_good(config.CODE_RELEASES)["release"]), B)
        self.assertEqual(Path(releases.previous_known_good(config.CODE_RELEASES)["release"]), self.versions["A"])
        self.assertServing(B)
        self.assertEqual(Path(d["previous_live"]["release"]), B)
        self.assertEqual(Path(d["final_live"]["release"]), B)
        self.assertTrue(d["completed_at"])
        self.assertEqual(queue.running(), [], "a finished job was left claimed")
        self.assertFalse(adm_lock.holder(config.LOCK)[0], "the deploy lock was left held")

    def test_01_first_two_deploys(self):
        self.ensure_ab()
        A, B = self.versions["A"], self.versions["B"]
        self.assertNotEqual(A, B)
        self.assertEqual(Path(releases.known_good(config.CODE_RELEASES)["release"]), B)
        self.assertEqual(Path(releases.previous_known_good(config.CODE_RELEASES)["release"]), A)
        d = journal.all_jobs()[-1]
        self.assertEqual([h["state"] for h in d["state_history"]],
                         ["created", "validating", "building", "built", "health_checking", "verified", "live"])
        self.assertEqual(Path(d["previous_live"]["release"]), A)
        self.assertEqual(d["attempted"]["version"], "vB")
        self.assertTrue(d["source"]["bundle_sha256"])
        self.assertTrue(d["process"]["pid"] and d["process"]["pgid"])
        self.assertServing(B)

    def test_02_broken_release_rolls_back_to_B_not_A(self):
        # PR #5 bug 3: A -> B -> broken C went back to A. It must go back to B, exactly.
        self.ensure_ab()
        job, verdict, d = self.deploy(self.bundle("vC", broken_gateway=True))
        self.assertEqual(verdict, "ROLLED_BACK", Path(d["log"]).read_text()[-3000:])
        self.assertEqual(d["state"], "rolled_back")
        self.assertIn("identity check", d["failure_reason"])
        self.assertEqual(d["rollback"]["result"], "succeeded")
        self.assertBLiveExactly(d)
        cand = Path(d["attempted"]["release"])
        self.assertFalse(cand.exists() and releases.is_verified(cand), "the failed release is still a candidate")

    def test_03_timeout_after_switch_stops_everything_then_restores_B(self):
        self.ensure_ab()
        job, verdict, d = self.deploy(self.bundle("vD", mode="hang_after_switch"), timeout=8)
        self.assertEqual(verdict, "ROLLED_BACK", Path(d["log"]).read_text()[-3000:])
        self.assertIn("timed_out", [h["state"] for h in d["state_history"]])
        self.assertTrue(d["process"]["timed_out"] and d["process"]["stopped"])
        self.assertBLiveExactly(d)

    def test_04_timeout_before_switch_needs_no_rollback(self):
        self.ensure_ab()
        job, verdict, d = self.deploy(self.bundle("vE", mode="hang_prepare"), timeout=4)
        self.assertEqual(verdict, "TIMED_OUT")
        self.assertEqual(d["rollback"]["result"], "not_needed")
        self.assertBLiveExactly(d)

    def test_05_escaped_daemon_is_stopped_too(self):
        self.ensure_ab()
        job, verdict, d = self.deploy(self.bundle("vF", mode="orphan_prepare"), timeout=4)
        self.assertEqual(verdict, "TIMED_OUT")
        self.assertFalse([p for p in proc._all_pids() if "atta-orphan-test" in proc.cmdline(p)
                          and proc._stat(p) and proc._stat(p)[4] != "Z"])
        self.assertBLiveExactly(d)

    def test_06_failure_while_preparing_touches_nothing(self):
        self.ensure_ab()
        job, verdict, d = self.deploy(self.bundle("vG", mode="fail_prepare"))
        self.assertEqual(verdict, "FAILED")
        self.assertEqual(d["rollback"]["result"], "not_needed")
        self.assertNotIn("health_checking", [h["state"] for h in d["state_history"]])
        self.assertBLiveExactly(d)

    def test_07_deploy_that_leaves_a_process_is_not_accepted(self):
        self.ensure_ab()
        job, verdict, d = self.deploy(self.bundle("vH", mode="leave_process"))
        self.assertEqual(verdict, "ROLLED_BACK", Path(d["log"]).read_text()[-3000:])
        self.assertIn("left processes running", d["failure_reason"])
        self.assertBLiveExactly(d)

    def test_08_second_deploy_while_one_runs_is_refused_and_starts_nothing(self):
        self.ensure_ab()
        z = self.bundle("vI", mode="hang_prepare")
        out = {}
        th = threading.Thread(target=lambda: out.update(r=self.deploy(z, timeout=6)))
        th.start()
        try:
            wait_until(lambda: adm_lock.holder(config.LOCK)[0], 10)
            # deployd's pass: must not claim a queued job while the lock is held
            queued = queue.enqueue(self.bundle("vJ"), origin="local", requested_by="incoming")
            import deployd
            self.assertIsNone(deployd.one_pass(manager.self_hash()))
            self.assertEqual([m["job_id"] for m in queue.pending()], [queued])
            # a direct second deploy is refused before anything happens
            with self.assertRaises(adm_lock.Busy):
                manager.process({"job_id": "x", "archive": str(z)})
        finally:
            th.join(60)
        self.assertEqual(out["r"][1], "TIMED_OUT")
        # the queued job still runs later, once — and deploys
        import deployd
        deployd.one_pass(manager.self_hash())
        self.assertEqual(deployment.get(queued)["verdict"], "DEPLOYED")
        self.versions["B-before-J"] = self.versions["B"]
        self.versions["A"], self.versions["B"] = self.versions["B"], self.live()

    def test_09_killed_deployctl_is_recovered_not_rerun(self):
        # PR #5 bug 7: a killed deploy was re-run while its installer still ran. Now: its leftovers hold the lock,
        # are recognised as abandoned (owner dead), stopped, the job marked interrupted, B restored — never re-run.
        self.ensure_ab()
        z = self.bundle("vK", mode="hang_after_switch")
        env = {**os.environ}
        # run deployctl in a child with the SAME configuration (paths) as this test
        cfg = {k: str(getattr(config, k)) for k in ("ROOT", "APP", "ADM", "INCOMING", "REQUESTS", "QUEUE", "RUNNING",
               "JOURNAL", "STAGING", "RELEASES", "BACKUPS", "LOGS", "CURRENT", "PREVIOUS", "LOCK", "KNOWN_GOOD",
               "CODE_RELEASES", "ENV_FILE", "PROXY_FILE", "PIPELINE_LOCK")}
        runner = self.t / "run_deployctl.py"
        runner.write_text(
            "import sys, json\nfrom pathlib import Path\n"
            f"sys.path.insert(0, {str(DEP / 'deployd')!r}); sys.path.insert(0, {str(DEP)!r})\n"
            "from adm import config, authz\n"
            f"for k, v in json.loads({json.dumps(json.dumps(cfg))}).items(): setattr(config, k, Path(v))\n"
            "config.DIRS=(config.INCOMING,config.QUEUE,config.RUNNING,config.JOURNAL,config.STAGING,config.RELEASES,"
            "config.BACKUPS,config.LOGS); config.PRIVATE_DIRS=(config.INCOMING,config.QUEUE,config.RUNNING)\n"
            "import os; config.TRUSTED_UID=os.geteuid(); config.SERVICES=[]; config.REQUIRE_PROXY=False;"
            " config.BROWSER_CHECK=False; config.RUN_TESTS=False; config.DEPLOY_TIMEOUT=600\n"
            "authz.USERS_FILE = config.ROOT/'state'/'users.json'\n"
            f"sys.argv=['deployctl','deploy',{str(z)!r}]\n"
            f"exec(compile(open({str(DEP / 'deployd' / 'deployctl')!r}).read(), 'deployctl', 'exec'))\n")
        ctl_log = open(self.t / "deployctl-killed.log", "wb")
        ctl = subprocess.Popen([sys.executable, str(runner)], env=env, stdout=ctl_log, stderr=subprocess.STDOUT)
        self.addCleanup(ctl_log.close)
        job = wait_until(lambda: next((d["job_id"] for d in journal.all_jobs()
                                       if d.get("state") == "health_checking"), None), 60)
        self.assertTrue(job, "the deploy never reached health_checking")
        time.sleep(1)
        ctl.kill(); ctl.wait()                          # deployctl dies; its installer tree lives on, holding the lock
        self.assertTrue(adm_lock.holder(config.LOCK)[0], "the orphaned installer should still hold the lock")
        self.assertTrue(proc.find_by_env("ATTA_DEPLOYMENT_ID", job))
        import deployd
        deployd.one_pass(manager.self_hash())          # reclaims the abandoned lock (stops the orphans)
        self.assertFalse(proc.find_by_env("ATTA_DEPLOYMENT_ID", job))
        deployd.one_pass(manager.self_hash())          # recovery: interrupted + rollback
        d = deployment.get(job)
        self.assertEqual(d["verdict"], "ROLLED_BACK", Path(d["log"]).read_text()[-3000:])
        self.assertIn("interrupted", [h["state"] for h in d["state_history"]])
        self.assertEqual(Path(d["log"]).read_text().count("=== bash run"), 1, "the killed job was run again")
        self.assertBLiveExactly(d)

    def test_10_deployd_stopping_interrupts_then_rolls_back_on_next_start(self):
        self.ensure_ab()
        stop = threading.Event()
        threading.Timer(4, stop.set).start()
        job = queue.enqueue(self.bundle("vL", mode="hang_after_switch"), origin="local", requested_by="deployctl",
                            claimed=True)
        meta = json.loads((config.RUNNING / f"{job}.json").read_text())
        verdict = manager.process(meta, cancel=stop.is_set, shutting_down=stop.is_set)
        self.assertEqual(verdict, "INTERRUPTED")
        d = deployment.get(job)
        self.assertEqual((d["state"], d["rollback"]["result"]), ("interrupted", "pending"))
        self.assertTrue([m for m in queue.running() if m["job_id"] == job], "the job must stay claimed for recovery")
        with adm_lock.DeployLock(config.LOCK, "restart") as lk:
            done = dict(manager.recover(lk))
        self.assertEqual(done[job], "ROLLED_BACK")
        self.assertBLiveExactly(deployment.get(job))

    def test_11_manual_rollback_goes_to_previous_known_good(self):
        self.ensure_ab()
        A, B = self.versions["A"], self.versions["B"]
        a_sha = releases.tree_sha256(A)
        with adm_lock.DeployLock(config.LOCK, "20990101T000000Z-m-manual-rollback") as lk:
            verdict = manager.manual_rollback("20990101T000000Z-m-manual-rollback",
                                              releases.previous_known_good(config.CODE_RELEASES), lk)
        d = deployment.get("20990101T000000Z-m-manual-rollback")
        self.assertEqual(verdict, "DEPLOYED", Path(d["log"]).read_text()[-3000:])
        self.assertEqual(releases.tree_sha256(self.live()), a_sha)       # A's code, exactly (content)
        self.assertEqual(Path(releases.previous_known_good(config.CODE_RELEASES)["release"]), B)
        self.versions["A"], self.versions["B"] = B, self.live()        # now B->A': keep the invariant for others


if __name__ == "__main__":
    unittest.main()
