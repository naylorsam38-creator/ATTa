"""v115 security regression tests. No Docker, no network, no root needed.

    cd ATTa && python3 -m unittest discover -s tests -v

Covers review items #3 (reserved names never grant authority), #2 (evidence is data, never ATTa UI) and
#1 (ATTa's own secrets never reach an app; an app's approved customer secrets still do).
Each test class works in throwaway folders; nothing on the machine is touched."""
import importlib, io, json, os, shutil, stat, sys, tempfile, unittest, zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
DEP = HERE.parent / "04-deployment"
TMP = Path(tempfile.mkdtemp(prefix="atta-v115-"))
# Only used if this file runs on its own; under discover the first test module's folders win.
os.environ.setdefault("APP_BUILDER_ROOT", str(TMP / "root"))
os.environ.setdefault("ATTA_ADM_ROOT", str(TMP / "adm"))
for p in (str(DEP), str(DEP / "deployd")):
    if p not in sys.path:
        sys.path.insert(0, p)

import accounts, alerts, builds, pipeline  # noqa: E402
from adm import config as adm_config, authz, manager, queue as adm_queue  # noqa: E402


def make_zip(path, files):
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        for name, data in files.items():
            z.writestr(name, data)
    return path


def write_users(**roles):
    """write_users(admin='admin', tester01='user', off=('admin', True)) with real password hashes."""
    d = {"schema": "APP_BUILDER_USERS.v1", "users": {}}
    for name, r in roles.items():
        role, disabled = (r, False) if isinstance(r, str) else r
        d["users"][name] = {"name": name, "role": role, "disabled": disabled, "session_version": 1,
                            "password": accounts.make_hash("correct horse battery staple")}
    accounts.save(d)


# ============================================================================ #3 reserved names

class ReservedNamesCannotBeAccounts(unittest.TestCase):
    def setUp(self):
        write_users(admin="admin")

    def test_create_refuses_reserved_names(self):
        for name in ("system", "incoming", "deployctl", "local", "root", "admin-test", "admin-sam"):
            with self.assertRaisesRegex(ValueError, "reserved", msg=name):
                accounts.create(name, "user")
            self.assertIsNone(accounts.get(name), name)          # nothing was written

    def test_normal_names_still_work(self):
        self.assertTrue(accounts.create("alice", "user"))
        self.assertTrue(accounts.create("administrator", "user"))   # only the admin- prefix is reserved
        self.assertTrue(accounts.create("systems-team", "user"))   # exact names only

    def test_is_reserved_normalises(self):
        self.assertTrue(accounts.is_reserved_name(" System "))
        self.assertTrue(accounts.is_reserved_name("ADMIN-x"))
        self.assertFalse(accounts.is_reserved_name("admin"))
        self.assertFalse(accounts.is_reserved_name(None))

    def test_existing_reserved_account_cannot_log_in(self):
        write_users(admin="admin", system="admin")
        self.assertIsNone(accounts.verify("system", "correct horse battery staple"))
        self.assertIsNotNone(accounts.verify("admin", "correct horse battery staple"))

    def test_existing_reserved_accounts_are_disabled_at_startup(self):
        write_users(admin="admin", system="admin", deployctl="user", alice="user", **{"admin-sam": "admin"})
        before = accounts.get("system")["session_version"]
        self.assertEqual(sorted(accounts.disable_reserved_accounts()), ["deployctl", "system"])
        self.assertTrue(accounts.get("system")["disabled"])
        self.assertGreater(accounts.get("system")["session_version"], before)   # its sessions end
        self.assertEqual(accounts.get("system")["disabled_reason"], "reserved account name (v115)")
        self.assertFalse(accounts.get("alice").get("disabled"))
        self.assertFalse(accounts.get("admin-sam").get("disabled"))   # a real admin is never locked out
        self.assertEqual(accounts.disable_reserved_accounts(), [])     # idempotent

    def test_cli_will_not_re_enable_a_reserved_account(self):
        write_users(admin="admin", system=("admin", True))
        self.assertEqual(accounts.main(["enable", "system"]), 1)
        self.assertTrue(accounts.get("system")["disabled"])

    def test_adm_keeps_the_same_reserved_set(self):
        self.assertEqual(set(authz.RESERVED_EXACT_NAMES), set(accounts.RESERVED_EXACT_NAMES))


class AdmJobsNeedAnOrigin(unittest.TestCase):
    def setUp(self):
        authz.USERS_FILE = accounts.USERS_FILE
        adm_config.TRUSTED_UID = os.getuid()   # unprivileged tests: the test user plays root
        adm_config.ensure_dirs()
        write_users(admin="admin", tester01="user", gone=("admin", True), system="admin")

    def _job(self, origin="local", who="deployctl"):
        z = make_zip(TMP / "job.zip", {"x.txt": "hi"})
        job = adm_queue.enqueue(z, origin=origin, requested_by=who)
        return [m for m in adm_queue.pending() if m["job_id"] == job][0]

    def _rewrite(self, meta, **changes):
        meta = {**meta, **changes}
        for k, v in list(meta.items()):
            if v is None:
                meta.pop(k)
        p = adm_config.QUEUE / f"{meta['job_id']}.json"
        p.write_text(json.dumps(meta))
        return meta

    def test_enqueue_requires_origin(self):
        z = make_zip(TMP / "e.zip", {"x.txt": "hi"})
        with self.assertRaises(TypeError):
            adm_queue.enqueue(z, requested_by="deployctl")            # no silent default any more
        with self.assertRaises(ValueError):
            adm_queue.enqueue(z, origin="system", requested_by="x")
        with self.assertRaises(ValueError):
            adm_queue.enqueue(z, origin="web", requested_by="")      # a web job must name its account

    def test_missing_or_unknown_origin_is_refused(self):
        m = self._job()
        self.assertFalse(authz.allowed(self._rewrite(m, origin=None))[0])
        ok, why = authz.allowed(self._rewrite(m, origin="system"))
        self.assertFalse(ok); self.assertIn("origin", why)

    def test_web_job_cannot_impersonate_a_local_requester(self):
        for who in ("deployctl", "incoming", "system", "local", "root"):
            ok, why = authz.allowed(self._job("web", who))
            self.assertFalse(ok, who)

    def test_web_job_needs_an_enabled_admin(self):
        self.assertTrue(authz.allowed(self._job("web", "admin"))[0])
        self.assertFalse(authz.allowed(self._job("web", "tester01"))[0])
        self.assertFalse(authz.allowed(self._job("web", "gone"))[0])
        self.assertFalse(authz.allowed(self._job("web", "nobody"))[0])
        self.assertFalse(authz.allowed(self._rewrite(self._job("web", "admin"), requested_by=None))[0])

    def test_queue_folder_must_be_private(self):
        m = self._job()
        self.assertTrue(authz.allowed(m)[0])
        adm_config.QUEUE.chmod(0o755)
        try:
            ok, why = authz.allowed(m)
            self.assertFalse(ok); self.assertIn("0700", why)
        finally:
            adm_config.QUEUE.chmod(0o700)

    def test_queue_owned_by_someone_else_is_refused(self):
        m = self._job()
        adm_config.TRUSTED_UID = os.getuid() + 1
        ok, why = authz.allowed(m)
        self.assertFalse(ok); self.assertIn("owned by uid", why)

    def test_group_writable_job_file_is_refused(self):
        m = self._job()
        (adm_config.QUEUE / f"{m['job_id']}.json").chmod(0o620)
        self.assertFalse(authz.allowed(m)[0])

    def test_hard_linked_job_file_is_refused(self):
        m = self._job()
        link = TMP / f"link-{m['job_id']}"
        os.link(adm_config.QUEUE / f"{m['job_id']}.json", link)
        try:
            ok, why = authz.allowed(m)
            self.assertFalse(ok); self.assertIn("hard links", why)
        finally:
            link.unlink()

    def test_symlinked_job_file_is_refused(self):
        m = self._job()
        p = adm_config.QUEUE / f"{m['job_id']}.json"
        real = TMP / f"real-{m['job_id']}.json"; shutil.move(p, real); p.symlink_to(real)
        self.assertFalse(authz.allowed(m)[0])

    def test_archive_outside_the_queue_is_refused(self):
        m = self._job()
        outside = make_zip(TMP / "outside.zip", {"x": "y"})
        ok, why = authz.allowed(self._rewrite(m, archive=str(outside)))
        self.assertFalse(ok); self.assertIn("bundle", why)

    def test_bad_job_id_is_refused(self):
        m = self._job()
        self.assertFalse(authz.allowed({**m, "job_id": "../x"})[0])
        self.assertFalse(authz.allowed("not a dict")[0])

    def test_refused_job_touches_nothing(self):
        m = self._job("web", "system")
        self.assertEqual(manager.process(m), "FAILED")
        j = json.loads((adm_config.JOURNAL / f"{m['job_id']}.json").read_text())
        self.assertIn("not authorised", json.dumps(j))
        self.assertFalse((adm_config.QUEUE / f"{m['job_id']}.json").exists())   # removed from the queue

    def test_incoming_is_ignored_while_others_can_write_it(self):
        z = make_zip(adm_config.INCOMING / "drop.zip", {"x": "y"})
        adm_config.INCOMING.chmod(0o777)
        try:
            self.assertEqual(adm_queue.sweep_incoming(), [])
            self.assertTrue(z.exists())                                  # left where it was
        finally:
            adm_config.INCOMING.chmod(0o700)
        jobs = adm_queue.sweep_incoming()
        self.assertEqual(len(jobs), 1)
        meta = [m for m in adm_queue.pending() if m["job_id"] == jobs[0]][0]
        self.assertEqual((meta["origin"], meta["requested_by"]), ("local", "incoming"))
        adm_queue.finish(meta)

    def test_ensure_dirs_makes_queue_and_incoming_private(self):
        adm_config.QUEUE.chmod(0o755); adm_config.INCOMING.chmod(0o755)
        adm_config.ensure_dirs()
        self.assertEqual(stat.S_IMODE(adm_config.QUEUE.stat().st_mode), 0o700)
        self.assertEqual(stat.S_IMODE(adm_config.INCOMING.stat().st_mode), 0o700)

    def tearDown(self):
        adm_config.TRUSTED_UID = os.getuid()
        for p in adm_config.QUEUE.glob("*"):
            if p.is_symlink() or p.is_file():
                p.unlink()


class PipelineTrustIsNotAName(unittest.TestCase):
    def setUp(self):
        adm_config.TRUSTED_UID = os.getuid()
        write_users(admin="admin", tester01="user", system="admin")
        pipeline.INBOX.mkdir(parents=True, exist_ok=True); pipeline.INBOX.chmod(0o755)

    def test_system_named_owner_is_not_trusted(self):
        self.assertFalse(pipeline.may_update_system("system"))
        self.assertFalse(pipeline.may_update_system("System"))
        self.assertFalse(pipeline.may_update_system(""))
        self.assertTrue(pipeline.may_update_system("admin"))
        self.assertTrue(pipeline.may_update_system(None, local=True))

    def test_inbox_drop_trust_follows_the_filesystem(self):
        b = make_zip(pipeline.INBOX / "dropped-by-hand.zip", {"x": "y"})
        try:
            self.assertTrue(pipeline._local_inbox_drop(b)[0])
            pipeline.INBOX.chmod(0o777)
            self.assertFalse(pipeline._local_inbox_drop(b)[0])
            pipeline.INBOX.chmod(0o755); b.chmod(0o666)
            self.assertFalse(pipeline._local_inbox_drop(b)[0])
        finally:
            pipeline.INBOX.chmod(0o755); b.unlink(missing_ok=True)

    def test_untrusted_inbox_drop_is_refused_before_anything_runs(self):
        inner = io.BytesIO()
        with zipfile.ZipFile(inner, "w") as z:
            z.writestr("out/install_all.py", "import os; os.system('touch /tmp/pwned')\n")
        b = make_zip(pipeline.INBOX / "evil.zip",
                     {"ATTa/03-ui-skins-capability-package/UI_Skin_Capability_OneShot_v2.zip": inner.getvalue()})
        pipeline.PKG.mkdir(parents=True, exist_ok=True); (pipeline.PKG / "marker").write_text("original")
        pipeline.INBOX.chmod(0o777)   # someone other than root could have put it there
        try:
            self.assertEqual(pipeline.process(b), builds.FAILED)
        finally:
            pipeline.INBOX.chmod(0o755); b.unlink(missing_ok=True)
        rec = builds.get(pipeline.LAST_BUILD)
        self.assertEqual(rec["owner"], pipeline.LOCAL_OWNER)
        self.assertEqual(rec["origin"], "unknown")
        self.assertTrue(rec["refused"])
        self.assertIn("not placed by root", rec["error"])
        self.assertEqual((pipeline.PKG / "marker").read_text(), "original")

    def test_trusted_inbox_drop_is_local(self):
        b = make_zip(pipeline.INBOX / "by-root.zip", {"README.md": "no app here"})
        try:
            pipeline.process(b)   # not an app: fails later, but the record shows who it acted for
        finally:
            b.unlink(missing_ok=True)
        rec = builds.get(pipeline.LAST_BUILD)
        self.assertEqual((rec["owner"], rec["origin"]), (pipeline.LOCAL_OWNER, "local"))
        self.assertFalse(rec.get("refused"))

    def test_build_without_owner_is_an_error_not_system(self):
        rec = builds.create("", origin="web")
        b = make_zip(pipeline.INBOX / f"{rec['id']}.zip", {"app/index.html": "<h1>x</h1>"})
        try:
            self.assertEqual(pipeline.process(b), builds.FAILED)
        finally:
            b.unlink(missing_ok=True)
        self.assertIn("names no owner", builds.get(rec["id"])["error"])

    def test_queue_system_update_marks_web_origin(self):
        calls = []
        orig_geteuid, orig_isdir = os.geteuid, Path.is_dir
        fake_q = type("Q", (), {"enqueue": staticmethod(lambda *a, **k: calls.append(k) or "job-1")})
        rec = builds.create("admin", origin="web")
        stage = TMP / "stage-web"; (stage / "04-deployment").mkdir(parents=True, exist_ok=True)
        (stage / "run").write_text(""); (stage / "release.json").write_text("{}")
        (stage / "04-deployment" / "bootstrap.sh").write_text("")
        import adm
        saved = sys.modules.get("adm.queue")
        try:
            os.geteuid = lambda: 0
            Path.is_dir = lambda self: True if str(self) == "/run/systemd/system" else orig_isdir(self)
            sys.modules["adm.queue"] = fake_q; adm.queue = fake_q
            self.assertEqual(pipeline.queue_system_update(Path("x.zip"), stage, rec["id"], builds.get(rec["id"])), "job-1")
        finally:
            os.geteuid, Path.is_dir = orig_geteuid, orig_isdir
            sys.modules["adm.queue"] = saved; adm.queue = saved
        self.assertEqual((calls[0]["origin"], calls[0]["requested_by"]), ("web", "admin"))

    def tearDown(self):
        pipeline.INBOX.chmod(0o755)


# ============================================================================ #2 evidence is data

import threading, urllib.error, urllib.request  # noqa: E402
from http.server import ThreadingHTTPServer  # noqa: E402


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None


def load_gateway():
    """Import the real gateway (it needs a session secret and at least one account at import)."""
    os.environ.setdefault("APP_BUILDER_SESSION_SECRET", "test-session-secret-" + "x" * 32)
    if not accounts.load()["users"]:
        write_users(admin="admin")
    import gateway
    return gateway


EVIDENCE_ROOT = builds.ROOT / "state" / "runner" / "evidence"   # where the watcher writes it (both versions)


class EvidenceIsDataNotATTaUI(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.gw = load_gateway()
        cls.srv = ThreadingHTTPServer(("127.0.0.1", 0), cls.gw.H)
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()
        cls.base = f"http://127.0.0.1:{cls.srv.server_address[1]}"
        cls.opener = urllib.request.build_opener(_NoRedirect)

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown(); cls.srv.server_close()

    def setUp(self):
        write_users(admin="admin", tester01="user", tester02="user")
        ev = EVIDENCE_ROOT / "evilapp"
        shutil.rmtree(ev, ignore_errors=True); ev.mkdir(parents=True)
        (ev / "page.html").write_text("<script>fetch('/upload',{method:'POST'})</script>")
        (ev / "screenshot.png").write_bytes(b"\x89PNG\r\n\x1a\n fake")
        (ev / "browser.json").write_text('{"console": []}')
        # tester01's build checked evilapp; tester02 has nothing to do with it.
        rec = builds.create("tester01", origin="web")
        builds.update(rec["id"], qualification=[{"app": "evilapp", "qualified": False}])

    def get(self, path, who):
        req = urllib.request.Request(self.base + path)
        if who:
            req.add_header("Cookie", "session=" + self.gw.token(accounts.get(who)))
        try:
            with self.opener.open(req) as r:
                return r.status, dict(r.headers), r.read()
        except urllib.error.HTTPError as e:
            return e.code, dict(e.headers), e.read()

    def test_page_html_is_plain_text_download(self):
        code, h, body = self.get("/evidence/evilapp/page.html", "admin")
        self.assertEqual(code, 200)
        self.assertTrue(h["Content-Type"].startswith("text/plain"))
        self.assertIn("attachment", h["Content-Disposition"])
        self.assertIn("sandbox", h["Content-Security-Policy"])
        self.assertEqual(h["X-Content-Type-Options"], "nosniff")
        self.assertIn(b"<script>", body)                     # delivered intact, as text

    def test_screenshot_and_json_stay_viewable(self):
        code, h, _ = self.get("/evidence/evilapp/screenshot.png", "admin")
        self.assertEqual((code, h["Content-Type"], h["Content-Disposition"]), (200, "image/png", "inline"))
        self.assertIn("sandbox", h["Content-Security-Policy"])
        code, h, _ = self.get("/evidence/evilapp/browser.json", "admin")
        self.assertEqual(code, 200); self.assertTrue(h["Content-Type"].startswith("application/json"))

    def test_owner_sees_it_stranger_gets_404(self):
        self.assertEqual(self.get("/evidence/evilapp/page.html", "tester01")[0], 200)
        self.assertEqual(self.get("/evidence/evilapp/page.html", "tester02")[0], 404)
        self.assertEqual(self.get("/evidence/nosuchapp/page.html", "tester02")[0], 404)   # same answer
        self.assertEqual(self.get("/evidence/evilapp/page.html", None)[0], 302)            # login first

    def test_unknown_and_traversal_names_are_404(self):
        (EVIDENCE_ROOT / "evilapp" / "other.txt").write_text("x")
        for path in ("/evidence/evilapp/other.txt", "/evidence/evilapp/..%2f..%2fusers.json",
                     "/evidence/../state/users.json", "/evidence/evilapp/../../users.json"):
            self.assertEqual(self.get(path, "admin")[0], 404, path)

    def test_unknown_files_would_be_opaque_downloads(self):
        h = self.gw.evidence_headers('x"\r\nSet-Cookie: a=b.bin')
        self.assertEqual(h["Content-Type"], "application/octet-stream")
        self.assertTrue(h["Content-Disposition"].startswith("attachment"))
        self.assertNotIn("\r", h["Content-Disposition"]); self.assertNotIn('x"', h["Content-Disposition"])

    def test_symlinks_out_of_the_evidence_folder_are_refused(self):
        secret = TMP / "secret.json"; secret.write_text('{"s": 1}')
        f = EVIDENCE_ROOT / "evilapp" / "browser.json"; f.unlink(); f.symlink_to(secret)
        self.assertEqual(self.get("/evidence/evilapp/browser.json", "admin")[0], 404)
        linked = EVIDENCE_ROOT / "linkedapp"
        linked.unlink(missing_ok=True); linked.symlink_to(EVIDENCE_ROOT / "evilapp")
        self.assertEqual(self.get("/evidence/linkedapp/page.html", "admin")[0], 404)

    def test_every_response_carries_the_site_headers(self):
        for path, who, code in (("/builds", "tester01", 200), ("/api/me", "tester01", 200),
                                ("/nope", "tester01", 404), ("/", None, 302)):
            c, h, _ = self.get(path, who)
            self.assertEqual(c, code, path)
            self.assertIn("frame-ancestors 'none'", h.get("Content-Security-Policy", ""), path)
            self.assertEqual(h.get("X-Content-Type-Options"), "nosniff", path)
            self.assertEqual(h.get("X-Frame-Options"), "DENY", path)

    def test_evidence_csp_replaces_the_site_csp(self):
        _, h, _ = self.get("/evidence/evilapp/page.html", "admin")
        self.assertNotIn("script-src", h["Content-Security-Policy"])

    def test_startup_disables_reserved_accounts_and_alerts(self):
        write_users(admin="admin", system="admin")
        before = set(alerts.ALERT_DIR.glob("*.json")) if alerts.ALERT_DIR.exists() else set()
        self.assertEqual(self.gw.disable_reserved_at_startup(), ["system"])
        new = [json.loads(p.read_text()) for p in set(alerts.ALERT_DIR.glob("*.json")) - before]
        self.assertEqual(len(new), 1)
        self.assertEqual(new[0]["accounts_disabled"], ["system"])
        self.assertNotIn("password", json.dumps(new[0]).lower())
        self.assertEqual(self.gw.disable_reserved_at_startup(), [])        # nothing left to do, no new alert


# ============================================================================ #1 ATTa secrets never reach an app

import base64, subprocess  # noqa: E402
from urllib.parse import quote  # noqa: E402
import app_runner, compose_guard as cg, trusted_apps  # noqa: E402

ATTA_SESSION = "atta-session-secret-" + "s" * 30
ATTA_ANTHROPIC = "sk-ant-atta-own-key-" + "a" * 30
CUSTOMER_ANTHROPIC = "sk-ant-customer-key-" + "c" * 30
CUSTOMER_STRIPE = "sk_test_customer_" + "9" * 24


def _have_compose():
    try:
        return subprocess.run(["docker", "compose", "version"], capture_output=True, timeout=30).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


HAVE_COMPOSE = _have_compose()
try:
    import yaml  # noqa: F401  (bootstrap.sh installs it on a server; the YAML fallback path needs it)
    HAVE_YAML = True
except ImportError:
    HAVE_YAML = False


class _SecretsWorld(unittest.TestCase):
    """ATTa's secrets in its .env and in this process's environment, as systemd loads them on a server."""
    def setUp(self):
        self.t = Path(tempfile.mkdtemp(dir=TMP))
        self.env_file = self.t / "atta.env"
        self.env_file.write_text(f"APP_BUILDER_PORT=8787\nAPP_BUILDER_SESSION_SECRET={ATTA_SESSION}\n"
                                 f"ANTHROPIC_API_KEY={ATTA_ANTHROPIC}\nCOOLIFY_TOKEN=coolify-token-123456\n")
        self.saved = (cg.ATTA_ENV_FILE, dict(os.environ))
        cg.ATTA_ENV_FILE = self.env_file
        os.environ.update(APP_BUILDER_SESSION_SECRET=ATTA_SESSION, ANTHROPIC_API_KEY=ATTA_ANTHROPIC,
                          COOLIFY_TOKEN="coolify-token-123456", DOCKER_CONFIG=str(self.t / "docker"))
        self.app = self.t / "library" / "shop"; self.app.mkdir(parents=True)

    def tearDown(self):
        cg.ATTA_ENV_FILE = self.saved[0]
        os.environ.clear(); os.environ.update(self.saved[1])


class ComposeGetsACleanEnvironment(_SecretsWorld):
    def test_env_for_holds_no_atta_secret(self):
        env = app_runner._env_for({"env": {"STRIPE_SECRET_KEY": CUSTOMER_STRIPE}})
        for name in ("APP_BUILDER_SESSION_SECRET", "ANTHROPIC_API_KEY", "COOLIFY_TOKEN"):
            self.assertNotIn(name, env)
        self.assertNotIn(ATTA_SESSION, json.dumps(env))
        self.assertEqual(env["STRIPE_SECRET_KEY"], CUSTOMER_STRIPE)     # the app's approved value is there
        self.assertIn("PATH", env)

    def test_every_docker_command_gets_a_clean_environment(self):
        seen = []
        real = subprocess.run
        def fake(cmd, **kw):
            seen.append(kw.get("env")); return subprocess.CompletedProcess(cmd, 0, "", "")
        app_runner.subprocess.run = fake
        try:
            app_runner.sh(["docker", "ps"])
            app_runner.sh(["/usr/bin/docker", "compose", "ls"])
        finally:
            app_runner.subprocess.run = real
        for env in seen:
            self.assertIsNotNone(env)
            self.assertNotIn(ATTA_SESSION, json.dumps(env))

    def test_blank_secret_is_generated_not_taken_from_the_server(self):
        f = self.app / "docker-compose.yml"
        f.write_text("services:\n  web:\n    image: x\n    environment:\n      ANTHROPIC_API_KEY: ${ANTHROPIC_API_KEY}\n")
        part = {"env": {}}
        app_runner._fill_blank_secrets(f, part, [])
        self.assertIn("ANTHROPIC_API_KEY", part["env"])
        self.assertNotEqual(part["env"]["ANTHROPIC_API_KEY"], ATTA_ANTHROPIC)   # v114.2 skipped it: ATTa's key leaked

    def test_customer_value_in_the_apps_env_is_kept(self):
        f = self.app / "docker-compose.yml"
        f.write_text("services:\n  web:\n    image: x\n    environment:\n      ANTHROPIC_API_KEY: ${ANTHROPIC_API_KEY}\n")
        (self.app / ".env").write_text(f"ANTHROPIC_API_KEY={CUSTOMER_ANTHROPIC}\n")
        part = {"env": {}}
        app_runner._fill_blank_secrets(f, part, [])
        self.assertNotIn("ANTHROPIC_API_KEY", part["env"])   # compose reads the app's own .env value


class ComposePathsStayInsideTheApp(_SecretsWorld):
    def check(self, cfg, **kw):
        cg.validate_paths(cfg, [self.app, self.t / "work"], **kw)

    def refused(self, cfg, **kw):
        with self.assertRaises(cg.ComposeSecurityError) as c:
            self.check(cfg, **kw)
        self.assertTrue(str(c.exception).startswith(cg.REFUSED_MARK))
        return str(c.exception)

    def test_env_file_outside_is_refused(self):
        self.refused({"services": {"web": {"env_file": [{"path": "/srv/app-builder/.env"}]}}})
        self.refused({"services": {"web": {"env_file": ["../../../../srv/app-builder/.env"]}}})
        self.refused({"services": {"web": {"env_file": str(self.env_file)}}})
        self.check({"services": {"web": {"env_file": [{"path": str(self.app / ".env")}, "config/app.env"]}}})

    def test_symlink_inside_the_app_is_resolved(self):
        (self.app / "looks-local.env").symlink_to(self.env_file)
        (self.app / "etc").symlink_to("/etc")
        self.refused({"services": {"web": {"env_file": [str(self.app / "looks-local.env")]}}})
        self.refused({"secrets": {"s": {"file": str(self.app / "etc" / "shadow")}}})

    def test_secrets_and_configs_files_outside_are_refused(self):
        self.refused({"secrets": {"s": {"file": "/etc/shadow"}}})
        self.refused({"configs": {"c": {"file": "../host/shadow"}}})
        self.check({"secrets": {"s": {"file": str(self.app / "secret.txt")}}, "configs": {"c": {"file": "conf/x"}}})

    def test_secret_sourced_from_the_runner_environment_is_refused(self):
        why = self.refused({"secrets": {"s": {"environment": "APP_BUILDER_SESSION_SECRET"}}})
        self.assertNotIn(ATTA_SESSION, why)

    def test_build_contexts_outside_are_refused(self):
        self.refused({"services": {"web": {"build": {"context": "/"}}}})
        self.refused({"services": {"web": {"build": "/srv/app-builder"}}})
        self.refused({"services": {"web": {"build": {"context": ".", "additional_contexts": {"x": "/srv/app-builder"}}}}})
        self.refused({"services": {"web": {"build": {"context": ".", "dockerfile": "/etc/Dockerfile"}}}})
        self.refused({"services": {"web": {"build": {"context": ".", "ssh": ["default"]}}}})
        self.check({"services": {"web": {"build": {"context": str(self.app), "dockerfile": "Dockerfile",
                                                    "additional_contexts": {"a": "service:base", "b": "docker-image://alpine:3",
                                                                            "c": str(self.app / "extra")}}}}})
        self.check({"services": {"web": {"build": {"context": "https://github.com/o/r.git#main"}}}})

    def test_bind_style_named_volume_is_refused(self):
        for opts in ({"type": "none", "o": "bind", "device": "/"}, {"type": "none", "o": "bind,rw", "device": "/etc"},
                     {"o": "rbind", "device": "/root"}, {"type": "none", "o": "bind"}, {"type": "bind", "device": "relative"}):
            self.refused({"volumes": {"host": {"driver": "local", "driver_opts": opts}}})
        self.check({"volumes": {"t": {"driver_opts": {"type": "tmpfs", "device": "tmpfs", "o": "size=100m"}}}})
        self.check({"volumes": {"in": {"driver_opts": {"type": "none", "o": "bind", "device": str(self.app / "data")}}}})
        self.check({"volumes": {"plain": {}, "named": None}})

    def test_networks_must_be_plain_bridges(self):
        for net in ({"driver": "macvlan"}, {"driver": "ipvlan"}, {"driver": "host"}, {"external": True},
                    {"driver": "bridge", "driver_opts": {"com.docker.network.bridge.name": "evil0"}}):
            self.refused({"networks": {"n": net}})
        self.check({"networks": {"default": {"name": "shop_default", "ipam": {}}, "back": {"driver": "bridge"}, "x": None}})
        self.check({"networks": {"n": {"external": True}}}, host_access=True)        # a trusted manager may

    def test_trusted_host_access_relaxes_volumes_only(self):
        self.check({"volumes": {"host": {"driver_opts": {"type": "none", "o": "bind", "device": "/"}}}}, host_access=True)
        self.refused({"services": {"web": {"env_file": ["/srv/app-builder/.env"]}}}, host_access=True)
        self.refused({"secrets": {"s": {"file": "/etc/shadow"}}}, host_access=True)

    def test_project_env_link_to_atta_is_refused(self):
        f = self.app / "docker-compose.yml"; f.write_text("services: {}\n")
        (self.app / ".env").symlink_to(self.env_file)
        with self.assertRaises(cg.ComposeSecurityError):
            cg.check_project_env(f, [self.app])

    def test_malformed_config_is_refused(self):
        self.refused("not a dict"); self.refused({"services": ["web"]})


class RenderedScanProtectsOnlyATTaSecrets(_SecretsWorld):
    def protected(self):
        return cg.protected_values()

    def test_protected_set_is_atta_secrets_only(self):
        p = self.protected()
        self.assertIn(ATTA_SESSION, p); self.assertIn(ATTA_ANTHROPIC, p); self.assertIn("coolify-token-123456", p)
        self.assertNotIn("8787", p)                                   # not secret, and too short
        self.assertTrue(all(":" in label or label == "Docker login" for label in p.values()))

    def test_atta_value_is_refused_wherever_it_hides(self):
        p = self.protected()
        b64 = base64.b64encode(ATTA_SESSION.encode()).decode()
        for cfg in ({"services": {"web": {"environment": {"LEAKED": ATTA_SESSION}}}},
                    {"services": {"web": {"command": ["sh", "-c", f"echo {ATTA_ANTHROPIC}"]}}},
                    {"services": {"web": {"labels": {"x": "coolify-token-123456"}}}},
                    {"services": {"web": {"environment": {ATTA_SESSION: "as a key"}}}},
                    {"services": {"web": {"environment": {"B64": b64}}}},
                    {"services": {"web": {"environment": {"B64": b64.rstrip("=")}}}},
                    {"services": {"web": {"environment": {"URL": "https://x/?k=" + quote(ATTA_SESSION, safe="")}}}}):
            with self.assertRaises(cg.ComposeSecurityError) as c:
                cg.scan_rendered(cfg, p)
            msg = str(c.exception)
            self.assertNotIn(ATTA_SESSION, msg); self.assertNotIn(ATTA_ANTHROPIC, msg)   # never echoes a value

    def test_customer_tokens_pass_even_under_atta_names(self):
        cfg = {"services": {"web": {"environment": {
            "ANTHROPIC_API_KEY": CUSTOMER_ANTHROPIC,          # same NAME as ATTa's key, different value
            "STRIPE_SECRET_KEY": CUSTOMER_STRIPE, "OPENAI_API_KEY": "sk-proj-" + "o" * 40,
            "PAYPAL_CLIENT_SECRET": "paypal-" + "p" * 30, "SMTP_PASSWORD": "mail-password-123",
            "FLAG": "true", "PORT": "8787"}}}}
        cg.scan_rendered(cfg, self.protected())

    def test_customer_value_identical_to_atta_key_is_refused(self):
        with self.assertRaises(cg.ComposeSecurityError):
            cg.scan_rendered({"services": {"web": {"environment": {"ANTHROPIC_API_KEY": ATTA_ANTHROPIC}}}}, self.protected())

    def test_docker_login_is_protected(self):
        d = self.t / "docker"; d.mkdir()
        auth = base64.b64encode(b"atta:dockerhub-token-abcdef").decode()
        (d / "config.json").write_text(json.dumps({"auths": {"https://index.docker.io/v1/": {"auth": auth}}}))
        p = self.protected()
        self.assertIn("dockerhub-token-abcdef", p); self.assertIn(auth, p)

    def test_docker_run_env_is_scanned_too(self):
        cg.scan_env({"STRIPE_SECRET_KEY": CUSTOMER_STRIPE}, self.protected())
        with self.assertRaises(cg.ComposeSecurityError):
            cg.scan_env({"X": ATTA_SESSION}, self.protected())

    def test_refusal_is_final_not_self_healed(self):
        dx = app_runner.diagnose(cg.REFUSED_MARK + " the app's configuration contains an ATTa secret (X)")
        self.assertEqual(dx, {"rule": "security.refused", "fix": "next_part"})


@unittest.skipUnless(HAVE_COMPOSE, "docker compose not installed")
class RealComposeEndToEnd(_SecretsWorld):
    """The real app_runner._render_compose with the real `docker compose config` (no Docker daemon needed)."""
    def setUp(self):
        super().setUp()
        self.saved_work = app_runner.WORK
        app_runner.WORK = self.t / "work"; app_runner.WORK.mkdir()
        trusted_apps.TRUSTED_UID = os.getuid()
        self.saved_trusted = trusted_apps.TRUSTED_FILE
        trusted_apps.TRUSTED_FILE = self.t / "trusted_apps.json"

    def tearDown(self):
        app_runner.WORK = self.saved_work
        trusted_apps.TRUSTED_FILE = self.saved_trusted
        super().tearDown()

    def render(self, compose, app_env=None):
        (self.app / "docker-compose.yml").write_text(compose)
        if app_env is not None:
            (self.app / ".env").write_text(app_env)
        part = {"kind": "compose", "file": "docker-compose.yml", "env": {}}
        notes = []
        ok, err = app_runner._render_compose("shop", self.app, part, notes)
        rendered = app_runner.WORK / "shop" / "compose.rendered.json"
        return ok, err, (rendered.read_text() if ok else ""), notes

    def test_interpolating_atta_secrets_gets_nothing(self):
        ok, err, out, _ = self.render("services:\n  web:\n    image: nginx:1\n    environment:\n"
                                      "      STOLEN: ${APP_BUILDER_SESSION_SECRET}\n      KEY: ${ANTHROPIC_API_KEY:-}\n"
                                      "      TOKEN: ${COOLIFY_TOKEN}\n")
        self.assertTrue(ok, err)
        for v in (ATTA_SESSION, ATTA_ANTHROPIC, "coolify-token-123456"):
            self.assertNotIn(v, out)
        stolen = json.loads(out)["services"]["web"]["environment"]["STOLEN"]
        self.assertNotEqual(stolen, ATTA_SESSION)                    # its own generated value, never ATTa's
        self.assertRegex(stolen, r"^[0-9a-f]{32}$")

    def test_customer_tokens_reach_the_app(self):
        ok, err, out, _ = self.render(
            "services:\n  web:\n    image: nginx:1\n    environment:\n      ANTHROPIC_API_KEY: ${ANTHROPIC_API_KEY}\n"
            "      STRIPE_SECRET_KEY: ${STRIPE_SECRET_KEY}\n",
            app_env=f"ANTHROPIC_API_KEY={CUSTOMER_ANTHROPIC}\nSTRIPE_SECRET_KEY={CUSTOMER_STRIPE}\n")
        self.assertTrue(ok, err)
        env = json.loads(out)["services"]["web"]["environment"]
        self.assertEqual(env["ANTHROPIC_API_KEY"], CUSTOMER_ANTHROPIC)
        self.assertEqual(env["STRIPE_SECRET_KEY"], CUSTOMER_STRIPE)
        self.assertNotIn(ATTA_ANTHROPIC, out)
        rendered = app_runner.WORK / "shop" / "compose.rendered.json"
        self.assertEqual(stat.S_IMODE(rendered.stat().st_mode), 0o600)   # customer tokens: runner-only

    def test_env_file_pointing_at_atta_env_is_refused(self):
        ok, err, _, _ = self.render(f"services:\n  web:\n    image: nginx:1\n    env_file: [{self.env_file}]\n")
        self.assertFalse(ok); self.assertIn(cg.REFUSED_MARK, err); self.assertIn("env_file", err)
        self.assertNotIn(ATTA_SESSION, err)
        self.assertFalse((app_runner.WORK / "shop" / "compose.rendered.json").exists())   # nothing to run

    def test_env_file_hidden_in_an_include_is_refused(self):
        (self.app / "inc").mkdir()
        (self.app / "inc" / "extra.yml").write_text(f"services:\n  side:\n    image: busybox\n    env_file: [{self.env_file}]\n")
        ok, err, _, _ = self.render("include:\n  - inc/extra.yml\nservices:\n  web:\n    image: nginx:1\n")
        self.assertFalse(ok); self.assertIn(cg.REFUSED_MARK, err)

    def test_host_file_secret_and_bind_volume_are_refused(self):
        ok, err, _, _ = self.render("services:\n  web:\n    image: nginx:1\n    secrets: [s]\n"
                                    f"secrets:\n  s:\n    file: {self.env_file}\n")
        self.assertFalse(ok); self.assertIn("secrets.s.file", err)
        ok, err, _, _ = self.render("services:\n  web:\n    image: nginx:1\n    volumes:\n      - hostvol:/data\n"
                                    "volumes:\n  hostvol:\n    driver: local\n    driver_opts: {type: none, o: bind, device: /}\n")
        self.assertFalse(ok); self.assertIn("named volume hostvol", err)   # (a one-letter name reads as a Windows drive)

    def test_atta_value_pasted_into_the_apps_env_is_refused(self):
        ok, err, _, _ = self.render("services:\n  web:\n    image: nginx:1\n    environment:\n      K: ${K}\n",
                                    app_env=f"K={ATTA_SESSION}\n")
        self.assertFalse(ok); self.assertIn("ATTa secret", err); self.assertNotIn(ATTA_SESSION, err)

    def test_outside_service_bind_is_still_dropped_not_refused(self):
        ok, err, out, notes = self.render("services:\n  web:\n    image: nginx:1\n    volumes:\n"
                                          "      - /var/run/docker.sock:/var/run/docker.sock\n      - ./data:/data\n")
        self.assertTrue(ok, err)
        self.assertNotIn("docker.sock", json.dumps(json.loads(out)["services"]["web"].get("volumes")))
        self.assertTrue(any("dropped host mount /var/run/docker.sock" in n for n in notes))

    def test_trusted_app_keeps_the_socket_but_not_atta_secrets(self):
        trusted_apps.trust("shop", "Portainer-style manager needs the Docker socket", by="admin")
        ok, err, out, notes = self.render("services:\n  web:\n    image: nginx:1\n    volumes:\n"
                                          "      - /var/run/docker.sock:/var/run/docker.sock\n    environment:\n"
                                          "      STOLEN: ${APP_BUILDER_SESSION_SECRET}\n")
        self.assertTrue(ok, err)
        self.assertIn("docker.sock", out)
        self.assertNotIn(ATTA_SESSION, out)
        self.assertTrue(any("host access ALLOWED" in n for n in notes))

    def test_legacy_global_switch_no_longer_grants_host_access(self):
        saved = app_runner.LEGACY_HOST_ACCESS_SWITCH; app_runner.LEGACY_HOST_ACCESS_SWITCH = True
        try:
            ok, err, out, notes = self.render("services:\n  web:\n    image: nginx:1\n    volumes:\n"
                                              "      - /var/run/docker.sock:/var/run/docker.sock\n")
        finally:
            app_runner.LEGACY_HOST_ACCESS_SWITCH = saved
        self.assertTrue(ok, err)
        self.assertNotIn("docker.sock", out)
        self.assertTrue(any("no longer grants host access" in n for n in notes))


    def test_env_file_via_extends_is_refused(self):
        (self.app / "base.yml").write_text(f"services:\n  b:\n    image: busybox\n    env_file: [{self.env_file}]\n")
        ok, err, _, _ = self.render("services:\n  web:\n    image: nginx:1\n    extends:\n      file: base.yml\n      service: b\n")
        self.assertFalse(ok); self.assertIn(cg.REFUSED_MARK, err)

    @unittest.skipUnless(HAVE_YAML, "the include-file check itself is the YAML check (PyYAML)")
    def test_include_from_outside_the_app_is_refused(self):
        outside = self.t / "outside.yml"; outside.write_text("services:\n  x:\n    image: busybox\n")
        ok, err, _, _ = self.render(f"include:\n  - {outside}\nservices:\n  web:\n    image: nginx:1\n")
        self.assertFalse(ok); self.assertIn(cg.REFUSED_MARK, err)

    def test_no_pyyaml_on_a_modern_compose_still_checks_env_files(self):
        real = cg.raw_env_files
        cg.raw_env_files = lambda _f: (_ for _ in ()).throw(ImportError("No module named 'yaml'"))
        try:
            ok, err, _, _ = self.render(f"services:\n  web:\n    image: nginx:1\n    env_file: [{self.env_file}]\n")
            self.assertFalse(ok); self.assertIn("env_file", err)            # pass 1 caught it
            ok, err, _, _ = self.render("services:\n  web:\n    image: nginx:1\n")
            self.assertTrue(ok, err)                                        # and a clean app still runs
        finally:
            cg.raw_env_files = real

@unittest.skipUnless(HAVE_COMPOSE and HAVE_YAML, "docker compose or PyYAML not installed")
class OlderComposeWithoutNoEnvResolution(RealComposeEndToEnd):
    """The same guarantees on a compose too old for `config --no-env-resolution` (the YAML fallback)."""
    def setUp(self):
        super().setUp()
        self.real_sh = app_runner.sh
        def old_compose_sh(cmd, **kw):
            if "--no-env-resolution" in cmd:
                return 1, "unknown flag: --no-env-resolution"
            return self.real_sh(cmd, **kw)
        app_runner.sh = old_compose_sh

    def tearDown(self):
        app_runner.sh = self.real_sh
        super().tearDown()

    def test_no_pyyaml_on_a_modern_compose_still_checks_env_files(self):
        self.skipTest("modern-compose case; this class is the old compose (see test_no_pyyaml_and_old_compose_fails_closed)")


    def test_no_pyyaml_and_old_compose_fails_closed(self):
        real = cg.raw_env_files
        def no_yaml(_f):
            raise ImportError("No module named 'yaml'")
        cg.raw_env_files = no_yaml
        try:
            ok, err, _, _ = self.render("services:\n  web:\n    image: nginx:1\n")
        finally:
            cg.raw_env_files = real
        self.assertFalse(ok); self.assertIn("PyYAML", err)

class TrustedAppsFailClosed(unittest.TestCase):
    def setUp(self):
        self.t = Path(tempfile.mkdtemp(dir=TMP))
        self.saved = (trusted_apps.TRUSTED_FILE, trusted_apps.TRUSTED_UID)
        trusted_apps.TRUSTED_FILE = self.t / "trusted_apps.json"; trusted_apps.TRUSTED_UID = os.getuid()

    def tearDown(self):
        trusted_apps.TRUSTED_FILE, trusted_apps.TRUSTED_UID = self.saved

    def test_trust_roundtrip_is_recorded(self):
        r = trusted_apps.trust("Portainer", "needs the socket", by="sam")
        self.assertEqual((r["approved_by"], r["reason"]), ("sam", "needs the socket"))
        self.assertTrue(trusted_apps.is_trusted("portainer"))
        self.assertEqual(stat.S_IMODE(trusted_apps.TRUSTED_FILE.stat().st_mode), 0o600)
        self.assertTrue(trusted_apps.untrust("portainer")); self.assertFalse(trusted_apps.is_trusted("portainer"))

    def test_reason_required(self):
        with self.assertRaises(ValueError):
            trusted_apps.trust("x", "  ")

    def test_writable_or_foreign_file_is_ignored(self):
        trusted_apps.trust("portainer", "socket")
        trusted_apps.TRUSTED_FILE.chmod(0o666)
        self.assertFalse(trusted_apps.is_trusted("portainer"))
        trusted_apps.TRUSTED_FILE.chmod(0o600)
        trusted_apps.TRUSTED_UID = os.getuid() + 1
        self.assertFalse(trusted_apps.is_trusted("portainer"))

    def test_garbage_file_is_ignored(self):
        trusted_apps.TRUSTED_FILE.write_text("{nope"); trusted_apps.TRUSTED_FILE.chmod(0o600)
        self.assertEqual(trusted_apps.load(), {})


if __name__ == "__main__":
    unittest.main()
