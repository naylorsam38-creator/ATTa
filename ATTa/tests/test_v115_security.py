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


if __name__ == "__main__":
    unittest.main()
