"""v117 (checklist B): a deployment is verified only when ATTA answers — not just something on the port.

    cd ATTa && python3 -m unittest tests.test_v117_health -v

Real gateway processes against real impostors: nginx's welcome page, a bare "OK" server, a web server faking
ATTa's JSON, a DIFFERENT ATTa installation (other secret), and an old release still holding the port."""
import json, subprocess, sys, unittest, urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from atta_testlib import DEP, NGINX_WELCOME, Gateway, Impostor, free_port, tmpdir, write_env  # noqa: E402
import atta_health, atta_identity  # noqa: E402


class Identity(unittest.TestCase):
    def test_proof_depends_on_secret_and_challenge(self):
        c = "a" * 40
        self.assertNotEqual(atta_identity.proof("s1", c), atta_identity.proof("s2", c))
        self.assertNotEqual(atta_identity.proof("s1", c), atta_identity.proof("s1", "b" * 40))
        self.assertTrue(atta_identity.verify("s1", c, atta_identity.proof("s1", c)))
        self.assertFalse(atta_identity.verify("s1", c, "0" * 64))

    def test_malformed_challenges_refused(self):
        for c in ("", "short", "x" * 129, "a" * 31 + ":", "../" * 12, "a" * 40 + "\n"):
            with self.subTest(c=c), self.assertRaises(ValueError):
                atta_identity.proof("s", c)
        with self.assertRaises(ValueError):
            atta_identity.proof("", "a" * 40)

    def test_proof_key_cannot_sign_a_session_cookie(self):
        # gateway cookies are HMAC(secret, "name:ver:ts:nonce"); the proof key is HMAC(secret, label) and the label
        # holds no ':' — no session value can equal it, so challenge answers never help forge a cookie.
        self.assertNotIn(b":", atta_identity.KEY_LABEL)

    def test_release_info(self):
        d = tmpdir("atta-rel-")
        (d / "release.json").write_text(json.dumps({"version": "v9", "features": [atta_identity.FEATURE]}))
        self.assertEqual(atta_identity.release_info(d)["version"], "v9")
        self.assertTrue(atta_identity.has_identity(d))
        (d / "code").mkdir()
        self.assertEqual(atta_identity.release_info(d / "code")["version"], "v9")   # bundle layout: one level up
        self.assertFalse(atta_identity.has_identity(tmpdir("atta-rel-none-")))


class AgainstRealGateway(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tmpdir("atta-health-")
        cls.gw = Gateway(cls.tmp / "a").start()

    @classmethod
    def tearDownClass(cls):
        cls.gw.stop()

    def test_identity_passes_and_names_the_release(self):
        d = atta_health.check_identity("http", "127.0.0.1", self.gw.port, "127.0.0.1", self.gw.secret)
        self.assertEqual(d["service"], atta_identity.SERVICE)
        self.assertEqual(d["release"], DEP.name)
        atta_health.check_login("http", "127.0.0.1", self.gw.port, "127.0.0.1")

    def test_plain_health_still_ok_with_identity_headers(self):
        # Older gates (and rollbacks to them) read the body "OK": kept, now with the service named.
        with urllib.request.urlopen(f"http://127.0.0.1:{self.gw.port}/health", timeout=5) as r:
            self.assertEqual(r.read().strip(), b"OK")
            self.assertEqual(r.headers["X-ATTa-Service"], atta_identity.SERVICE)
            self.assertEqual(r.headers["X-ATTa-Instance"], atta_identity.instance_id(self.gw.secret))

    def test_bad_challenge_is_400_not_a_proof(self):
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{self.gw.port}/health?challenge=short", timeout=5)
            self.fail("expected 400")
        except urllib.error.HTTPError as e:
            self.assertEqual(e.code, 400)
            self.assertNotIn("proof", json.loads(e.read()))

    def test_wrong_secret_means_another_installation(self):
        with self.assertRaisesRegex(atta_health.CheckFailed, "wrong proof"):
            atta_health.check_identity("http", "127.0.0.1", self.gw.port, "127.0.0.1", "not-this-installation")

    def test_stale_release_on_the_port_is_detected(self):
        with self.assertRaisesRegex(atta_health.CheckFailed, "expected 'v999-new'"):
            atta_health.check_identity("http", "127.0.0.1", self.gw.port, "127.0.0.1", self.gw.secret,
                                       expect_release="v999-new")

    def test_run_checks_reads_port_from_env_file(self):
        ok, lines = atta_health.run_checks(self.gw.env_file, timeout=5)
        self.assertTrue(ok, lines)
        self.assertIn(f":{self.gw.port}", lines[0])

    def test_cli_last_line(self):
        r = subprocess.run([sys.executable, str(DEP / "atta_health.py"), "--env", str(self.gw.env_file),
                            "--timeout", "5"], capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(r.stdout.strip().splitlines()[-1], "HEALTH PASS")

    def test_second_installation_on_other_port_is_not_this_one(self):
        other = Gateway(self.tmp / "b").start()
        try:
            env = write_env(self.tmp / "mixed.env", APP_BUILDER_PORT=other.port,
                            APP_BUILDER_SESSION_SECRET=self.gw.secret)
            ok, lines = atta_health.run_checks(env, timeout=2, interval=0.5)
            self.assertFalse(ok)
            self.assertIn("wrong proof", lines[-1])
        finally:
            other.stop()


class Impostors(unittest.TestCase):
    def _fails(self, imp, pattern):
        try:
            env = write_env(tmpdir("atta-imp-") / ".env", APP_BUILDER_PORT=imp.port,
                            APP_BUILDER_SESSION_SECRET="s" * 48)
            ok, lines = atta_health.run_checks(env, timeout=1, interval=0.3)
            self.assertFalse(ok, lines)
            self.assertRegex(lines[-1], pattern)
        finally:
            imp.stop()

    def test_nginx_welcome_page(self):
        self._fails(Impostor(NGINX_WELCOME), "Welcome to nginx")

    def test_bare_ok_server(self):
        self._fails(Impostor("OK\n", ctype="text/plain"), "not ATTa's")

    def test_fake_json_without_valid_proof(self):
        fake = json.dumps({"ok": True, "service": atta_identity.SERVICE, "proof": "0" * 64, "release": "x"})
        self._fails(Impostor(fake, ctype="application/json", headers={"X-ATTa-Service": atta_identity.SERVICE}),
                    "wrong proof")

    def test_json_from_another_service(self):
        self._fails(Impostor(json.dumps({"status": "ok"}), ctype="application/json"), "different service")

    def test_error_status(self):
        self._fails(Impostor("nope", status=503), "answered 503")

    def test_nothing_listening(self):
        env = write_env(tmpdir("atta-imp-") / ".env", APP_BUILDER_PORT=free_port(), APP_BUILDER_SESSION_SECRET="s" * 48)
        ok, lines = atta_health.run_checks(env, timeout=1, interval=0.3)
        self.assertFalse(ok)
        self.assertIn("no answer", lines[-1])

    def test_login_page_of_something_else(self):
        imp = Impostor("<html><title>Login</title><form method=post action=/login><input type=password></form></html>")
        try:
            with self.assertRaisesRegex(atta_health.CheckFailed, "not ATTa's login page"):
                atta_health.check_login("http", "127.0.0.1", imp.port, "127.0.0.1")
        finally:
            imp.stop()

    def test_redirect_is_not_followed(self):
        imp = Impostor("", status=302, headers={"Location": "http://example.com/login"})
        try:
            with self.assertRaisesRegex(atta_health.CheckFailed, "answered 302"):
                atta_health.check_login("http", "127.0.0.1", imp.port, "127.0.0.1")
        finally:
            imp.stop()

    def test_empty_secret_refused_before_asking(self):
        env = write_env(tmpdir("atta-imp-") / ".env", APP_BUILDER_PORT=free_port(), APP_BUILDER_SESSION_SECRET="")
        ok, lines = atta_health.run_checks(env, timeout=0)
        self.assertFalse(ok)
        self.assertIn("SESSION_SECRET is empty", lines[-1])


if __name__ == "__main__":
    unittest.main()
