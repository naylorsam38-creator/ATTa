"""v114 hardening tests. No Docker, no network, no root needed.

    cd ATTa && python3 -m unittest discover -s tests -v

Each test gets its own throwaway APP_BUILDER_ROOT, so nothing on the machine is touched."""
import importlib, io, json, os, shutil, sys, tempfile, unittest, zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
DEP = HERE.parent / "04-deployment"
TMP = Path(tempfile.mkdtemp(prefix="atta-v114-"))
os.environ["APP_BUILDER_ROOT"] = str(TMP / "root")
os.environ["ATTA_ADM_ROOT"] = str(TMP / "adm")
sys.path[:0] = [str(DEP), str(DEP / "deployd")]

import accounts, builds, pipeline, maintenance, app_runner, redact  # noqa: E402
from adm import config as adm_config, staging, authz, manager, queue as adm_queue  # noqa: E402


def make_zip(path, files):
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        for name, data in files.items():
            z.writestr(name, data)
    return path


def users(**roles):
    """Write an accounts file: users(admin='admin', tester01='user', off=('admin', True))."""
    d = {"users": {}}
    for name, r in roles.items():
        role, disabled = (r, False) if isinstance(r, str) else r
        d["users"][name] = {"role": role, "disabled": disabled, "hash": "x"}
    accounts.USERS_FILE.parent.mkdir(parents=True, exist_ok=True)
    accounts.USERS_FILE.write_text(json.dumps(d))


class SystemUpdateIsAdminOnly(unittest.TestCase):
    def setUp(self):
        users(admin="admin", tester01="user", gone=("admin", True))

    def test_roles(self):
        # v115: decided by the record's origin, not the name (see test_v115_security.py for the attacks).
        web = lambda who: {"origin": "web", "owner": who}
        self.assertTrue(pipeline.may_update_system(web("admin")))
        self.assertTrue(pipeline.may_update_system({"origin": "local", "owner": "system", "local_verified": True}))
        self.assertFalse(pipeline.may_update_system(web("system")))  # the name alone is worth nothing now
        self.assertFalse(pipeline.may_update_system(web("tester01")))
        self.assertFalse(pipeline.may_update_system(web("gone")))    # disabled admin
        self.assertFalse(pipeline.may_update_system(web("nobody")))
        self.assertFalse(pipeline.may_update_system(None))

    def _bundle_upload(self, owner):
        rec = builds.create(owner, origin="web", bundle_bytes=1, original_name="ATTa-evil.zip")
        inner = io.BytesIO()
        with zipfile.ZipFile(inner, "w") as z:
            z.writestr("out/install_all.py", "import os; os.system('touch /tmp/pwned')\n")
        b = pipeline.INBOX / f"{rec['id']}.zip"
        b.parent.mkdir(parents=True, exist_ok=True)
        make_zip(b, {"ATTa/03-ui-skins-capability-package/UI_Skin_Capability_OneShot_v2.zip": inner.getvalue(),
                     "ATTa/02-front-door/front-door.html": "<script>steal()</script>"})
        return rec["id"], b

    def test_tester_bundle_changes_nothing(self):
        pipeline.PKG.mkdir(parents=True, exist_ok=True)
        (pipeline.PKG / "marker").write_text("original package")
        front = pipeline.ROOT / "front-door.html"
        front.write_text("original front door")
        bid, b = self._bundle_upload("tester01")
        st = pipeline.process(b)
        self.assertEqual(st, builds.FAILED)
        rec = builds.get(bid)
        self.assertTrue(rec.get("refused"))
        self.assertIn("admin", json.dumps(rec))
        self.assertEqual((pipeline.PKG / "marker").read_text(), "original package")   # package not replaced
        self.assertEqual(front.read_text(), "original front door")                    # front door not replaced
        self.assertFalse((pipeline.PKG / "out" / "install_all.py").exists())          # nothing to run
        self.assertEqual(maintenance.on_failure(bid), "REFUSED")                      # not sent to self-healing

    def test_queue_system_update_second_check(self):
        rec = builds.create("tester01", origin="web", bundle_bytes=1)
        stage = TMP / "stage-q"; (stage / "04-deployment").mkdir(parents=True, exist_ok=True)
        (stage / "run").write_text(""); (stage / "release.json").write_text("{}")
        (stage / "04-deployment" / "bootstrap.sh").write_text("")
        self.assertIsNone(pipeline.queue_system_update(Path("x.zip"), stage, rec["id"], builds.get(rec["id"])))
        self.assertIn("admin account", builds.get(rec["id"])["adm"]["reason"])


class AdmAuthorisation(unittest.TestCase):
    def setUp(self):
        authz.USERS_FILE = accounts.USERS_FILE
        users(admin="admin", tester01="user")

    def _meta(self, who, origin):
        z = make_zip(TMP / "a.zip", {"x.txt": "hi"})
        job = adm_queue.enqueue(z, requested_by=who, origin=origin)
        return [m for m in adm_queue.pending() if m["job_id"] == job][0]

    def tearDown(self):
        for m in adm_queue.pending():
            adm_queue.finish(m)

    def test_allowed(self):
        for who, origin in (("deployctl", "local"), ("incoming", "local"), ("system", "local"), ("admin", "web")):
            self.assertTrue(authz.allowed(self._meta(who, origin))[0], who)
        for who, origin in (("tester01", "web"), ("nobody", "web"), ("system", "web"), ("admin", "local")):
            self.assertFalse(authz.allowed(self._meta(who, origin))[0], who)

    def test_unreadable_accounts_refuses_web_jobs(self):
        accounts.USERS_FILE.write_text("{not json")
        self.assertFalse(authz.allowed(self._meta("admin", "web"))[0])
        self.assertTrue(authz.allowed(self._meta("deployctl", "local"))[0])

    def test_non_admin_job_touches_nothing(self):
        z = make_zip(TMP / "b.zip", {"x.txt": "hi"})
        job = adm_queue.enqueue(z, build_id="b-1", requested_by="tester01", origin="web")
        meta = [m for m in adm_queue.pending() if m["job_id"] == job][0]
        self.assertEqual(manager.process(meta), "FAILED")
        j = json.loads((adm_config.JOURNAL / f"{job}.json").read_text())
        self.assertIn("not authorised", json.dumps(j))
        self.assertFalse(any(adm_config.BACKUPS.iterdir()) if adm_config.BACKUPS.exists() else False)


class AdmStagingLimits(unittest.TestCase):
    def setUp(self):
        self.saved = (adm_config.MAX_BUNDLE_BYTES, adm_config.MAX_BUNDLE_FILES, adm_config.MAX_COMPRESSION_RATIO)
        adm_config.ensure_dirs()

    def tearDown(self):
        adm_config.MAX_BUNDLE_BYTES, adm_config.MAX_BUNDLE_FILES, adm_config.MAX_COMPRESSION_RATIO = self.saved

    def test_traversal(self):
        z = make_zip(TMP / "t.zip", {"../../etc/evil": "x"})
        with self.assertRaisesRegex(staging.BundleRejected, "unsafe path"):
            staging.extract(z, "t1")

    def test_zip_bomb_ratio(self):
        z = make_zip(TMP / "bomb.zip", {"a.bin": b"\0" * (50 * 1024**2)})
        with self.assertRaisesRegex(staging.BundleRejected, "compression ratio"):
            staging.extract(z, "t2")

    def test_total_size(self):
        adm_config.MAX_BUNDLE_BYTES = 1000
        z = make_zip(TMP / "big.zip", {"a.txt": os.urandom(800).hex()})
        with self.assertRaisesRegex(staging.BundleRejected, "limit"):
            staging.extract(z, "t3")

    def test_file_count(self):
        adm_config.MAX_BUNDLE_FILES = 5
        z = make_zip(TMP / "many.zip", {f"f{i}": "x" for i in range(10)})
        with self.assertRaisesRegex(staging.BundleRejected, "too many files"):
            staging.extract(z, "t4")

    def test_normal_bundle_still_stages(self):
        z = make_zip(TMP / "ok.zip", {"ATTa/run": "echo", "ATTa/release.json": '{"version":"t"}',
                                      "ATTa/04-deployment/x.py": "print(1)\n"})
        d = staging.extract(z, "t5")
        self.assertTrue((d / "ATTa" / "04-deployment" / "x.py").is_file())
        staging.discard("t5")


class ContainerHardening(unittest.TestCase):
    def test_docker_run_flags(self):
        a = app_runner.harden_args({})
        for flag in ("no-new-privileges", "--pids-limit", "--cpus", "--cap-drop"):
            self.assertIn(flag, a)
        self.assertEqual(a[a.index("--cap-drop") + 1], "ALL")
        self.assertIn("NET_BIND_SERVICE", a)
        self.assertNotIn("NET_RAW", a)

    def test_relaxed_keeps_other_limits(self):
        a = app_runner.harden_args({"relaxed_caps": True})
        self.assertNotIn("--cap-drop", a)
        self.assertIn("no-new-privileges", a)
        self.assertIn("--pids-limit", a)

    def test_compose_service_loses_host_access(self):
        d = TMP / "app"; d.mkdir(exist_ok=True)
        s = {"image": "x", "privileged": True, "network_mode": "host", "pid": "host", "devices": ["/dev/sda"],
             "cap_add": ["SYS_ADMIN", "NET_ADMIN", "CHOWN"], "security_opt": ["seccomp:unconfined"],
             "volumes": [{"type": "bind", "source": "/var/run/docker.sock", "target": "/var/run/docker.sock"},
                         {"type": "bind", "source": "/", "target": "/host"},
                         {"type": "bind", "source": "/etc/localtime", "target": "/etc/localtime", "read_only": True},
                         {"type": "bind", "source": str(d / "conf"), "target": "/conf"},
                         {"type": "volume", "source": "data", "target": "/data"}]}
        notes = []
        app_runner.harden_service("web", s, {}, notes, d)
        for k in ("privileged", "network_mode", "pid", "devices"):
            self.assertNotIn(k, s)
        srcs = [v["source"] for v in s["volumes"]]
        self.assertNotIn("/var/run/docker.sock", srcs)
        self.assertNotIn("/", srcs)
        self.assertIn("/etc/localtime", srcs)          # read-only time zone: allowed
        self.assertIn(str(d / "conf"), srcs)           # inside the app: allowed
        self.assertIn("data", srcs)                    # named volume: allowed
        self.assertNotIn("SYS_ADMIN", s["cap_add"]); self.assertNotIn("NET_ADMIN", s["cap_add"])
        self.assertEqual(s["cap_drop"], ["ALL"])
        self.assertIn("no-new-privileges:true", s["security_opt"])
        self.assertFalse(any("unconfined" in o for o in s["security_opt"]))
        self.assertTrue(s["pids_limit"] and s["cpus"])

    def test_caps_rule_is_last_and_skippable(self):
        # A specific rule still wins over the generic "not permitted" one.
        dx = app_runner.diagnose("setrlimit: operation not permitted")
        self.assertEqual(dx["fix"], "drop_ulimits")
        dx = app_runner.diagnose("chown: /data: Operation not permitted")
        self.assertEqual(dx["fix"], "relax_caps")
        dx = app_runner.diagnose("chown: /data: Operation not permitted", skip=("container.caps",))
        self.assertTrue(dx is None or dx["fix"] != "relax_caps")

    def test_recipe_remembers_relaxed_caps(self):
        app_runner.RECIPES.mkdir(parents=True, exist_ok=True)
        app_runner.save_recipe("demo", {"kind": "image", "image": "x", "relaxed_caps": True}, "test")
        saved = json.loads((app_runner.RECIPES / "demo.json").read_text())
        self.assertTrue(saved.get("relaxed_caps"))


class Redaction(unittest.TestCase):
    def test_redacts(self):
        text = ("DB_PASSWORD=hunter2\nAPI_KEY: abc123def\n\"SECRET_KEY\": \"s3cr3t\"\n"
                "postgres://app:pa55@db:5432/x\nAuthorization: Bearer abcdefghijklmnop123\nAuthorization: token ghtok99999\n"
                "AKIAABCDEFGHIJKLMNOP sk-ant-api03-abcdefghijklmnopqrstuvwxyz\n"
                "-----BEGIN RSA PRIVATE KEY-----\nMIIE\n-----END RSA PRIVATE KEY-----\n"
                'SMTP_PASSWORD="two words"')
        out = redact.redact(text)
        for secret in ("hunter2", "abc123def", "s3cr3t", "pa55", "abcdefghijklmnop123", "AKIAABCDEFGHIJKLMNOP",
                       "sk-ant-api03", "MIIE", "ghtok99999", "two words", "words"):
            self.assertNotIn(secret, out, secret)
        self.assertIn("DB_PASSWORD", out)             # the name stays, so the model can still reason about it

    def test_leaves_ordinary_text(self):
        t = "Listening on port 8080\nGET / 200\nerror: module 'x' not found\nPASS: all six stages\nauthor: Jane"
        self.assertEqual(redact.redact(t), t)

    def test_marker_detection(self):
        self.assertTrue(redact.contains_mark({"content": "PASSWORD=" + redact.MARK}))
        self.assertFalse(redact.contains_mark({"content": "fine"}))


class LlmRepairNeverLeaksOrWritesBackSecrets(unittest.TestCase):
    """The repair loop with a fake model: what it is sent is redacted, and a write carrying the marker is refused."""
    def test_loop(self):
        import types, llm_repair
        sent, ran = [], []

        class Block:
            def __init__(self, **kw): self.__dict__.update(kw)

        replies = [
            [Block(type="tool_use", id="t1", name="write_overlay_file",
                   input={"app": "demo", "path": ".env", "content": "DB_PASSWORD=" + redact.MARK})],
            [Block(type="text", text="done")],
        ]

        class Messages:
            def create(self, **kw):
                sent.append(json.dumps(kw["messages"], default=lambda o: o.__dict__))
                content = replies.pop(0)
                return Block(stop_reason="tool_use" if any(b.type == "tool_use" for b in content) else "end_turn",
                             content=content)

        fake = types.ModuleType("anthropic")
        fake.Anthropic = lambda: Block(beta=Block(messages=Messages()))
        sys.modules["anthropic"] = fake
        saved = (llm_repair.available, llm_repair._budget_ok, llm_repair.ra.run)
        llm_repair.available = lambda: (True, "")
        llm_repair._budget_ok = lambda: True
        llm_repair.ra.run = lambda name, args: ran.append(name) or "ok"
        try:
            rec = {"id": "b-1", "owner": "admin", "state": "FAILED",
                   "error": "boot failed: DB_PASSWORD=hunter2 postgres://u:pw9@db/x"}
            out = llm_repair.attempt("build", {"error": rec["error"]}, rec, [])
        finally:
            llm_repair.available, llm_repair._budget_ok, llm_repair.ra.run = saved
            sys.modules.pop("anthropic", None)
        self.assertNotIn("hunter2", sent[0]); self.assertNotIn("pw9", sent[0])
        self.assertEqual(ran, [])                                    # the write never reached the tool
        self.assertTrue(out["actions"] and out["actions"][0]["error"])
        self.assertIn("refused", out["actions"][0]["result"])


if __name__ == "__main__":
    unittest.main()
