"""v121: journey_author.py — the half that feeds the gate.

Two kinds of test:
  * LIVE (the real thing): run only when a real app is up and its URL is in the environment —
        ATTA_LIVE_GOTIFY_URL   a Gotify server started with GOTIFY_DEFAULTUSER_NAME/PASS (env_admin class)
        ATTA_LIVE_GITEA_URL    a Gitea at first-run (install wizard) or installed (register form) (signup class)
        ATTA_LIVE_NOLOGIN_URL  a display-only app with nothing to click (e.g. Glances web): SMOKE_NO_CONTROL
    Each authors a contract against the real app, from a clean account store, and dry-runs it with the
    watcher's own code. These were run here on 2026-09-28 against Gotify 2.6.3, Gitea 1.24.5 and
    Glances 4 as real local processes; the change log records the outcomes.
  * LOGIC (real code, no app): credential store resolution, error contracts, submit selection on a page
    the test serves, env pair discovery, action validation, the fleet report.
Run: python3 -m unittest tests.test_v121_journey_author
"""
import importlib, json, os, re, shutil, sys, tempfile, unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
DEP = HERE / "04-deployment"
sys.path.insert(0, str(DEP))


class _Scratch(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        os.environ["APP_BUILDER_ROOT"] = str(self.root)
        for m in ("system_watcher", "journey_author"):
            if m in sys.modules: del sys.modules[m]
        self.w = importlib.import_module("system_watcher")
        self.ja = importlib.import_module("journey_author")

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)


class CredentialStore(_Scratch):
    def test_store_reference_resolves_from_the_accounts_file_and_never_leaks(self):
        self.ja.save_account("memos", "qa", "s3cret", "signup")
        f = self.root / "state" / "accounts" / "memos.json"
        self.assertEqual(oct(f.stat().st_mode & 0o777), "0o600")
        spec = {"username": "store:memos", "password": "store:memos"}
        self.assertEqual(self.w._credential(spec, "username"), "qa")
        self.assertEqual(self.w._credential(spec, "password"), "s3cret")
        self.assertNotIn("s3cret", json.dumps(spec))
        self.assertEqual(self.w._credential({"password": "store:nope"}, "password"), "")

    def test_forget_account(self):
        self.ja.save_account("x", "a", "b", "env_admin")
        self.ja.forget_account("x")
        self.assertIsNone(self.ja.load_account("x"))


class ErrorContracts(_Scratch):
    def test_authoring_failure_is_a_stage_6_code_with_the_cause(self):
        code, detail = self.w.run_user_journey(None, {"error": "ACCOUNT_NO_STRATEGY", "detail": "why"}, "u")
        self.assertEqual(code, "JOURNEY_NOT_AUTHORED:ACCOUNT_NO_STRATEGY")
        self.assertEqual(detail, "why")


class EnvPairs(_Scratch):
    def test_admin_pairs_are_found_by_name_shape_not_app_name(self):
        pairs = self.ja._env_pairs("anything", {"env": {
            "GOTIFY_DEFAULTUSER_NAME": "admin", "GOTIFY_DEFAULTUSER_PASS": "p1",
            "GF_SECURITY_ADMIN_USER": "grafana", "GF_SECURITY_ADMIN_PASSWORD": "p2",
            "PGADMIN_SETUP_EMAIL": "a@b.c", "PGADMIN_SETUP_PASSWORD": "p3",
            "DB_PASSWORD": "never", "PORT": "80"}})
        got = {(u, p) for u, p, _ in pairs}
        self.assertIn(("admin", "p1"), got)
        self.assertIn(("grafana", "p2"), got)
        self.assertIn(("a@b.c", "p3"), got)
        self.assertNotIn(("admin", "never"), {(u, p) for u, p, _ in pairs[:3]})   # prefix-matched pairs come first

    def test_dot_env_and_rendered_compose_are_read(self):
        (self.root / "library" / "app").mkdir(parents=True)
        (self.root / "library" / "app" / ".env").write_text("ADMIN_USER=root\nADMIN_PASSWORD=pw\n")
        (self.root / "state" / "runner" / "work" / "app").mkdir(parents=True)
        (self.root / "state" / "runner" / "work" / "app" / "compose.rendered.json").write_text(json.dumps(
            {"services": {"web": {"environment": ["INITIAL_ADMIN_EMAIL=e@x", "INITIAL_ADMIN_PASSWORD=zz"]}}}))
        got = {(u, p) for u, p, _ in self.ja._env_pairs("app", {})}
        self.assertIn(("root", "pw"), got)
        self.assertIn(("e@x", "zz"), got)


class SubmitSelection(unittest.TestCase):
    """The watcher must press the LOGIN form's button, never the first button on the page."""
    def test_login_submit_is_inside_the_password_form(self):
        from playwright.sync_api import sync_playwright
        sys.modules.pop("system_watcher", None)
        w = importlib.import_module("system_watcher")
        exe = os.environ.get("ATTA_CHROMIUM", "/usr/bin/chromium")
        with sync_playwright() as p:
            b = p.chromium.launch(headless=True, executable_path=exe) if Path(exe).exists() else p.chromium.launch(headless=True)
            pg = b.new_page()
            pg.set_content('<button id="menu">☰</button><form id="f"><input id="u"><input id="p" type="password">'
                           '<button id="go">Log in</button></form><button id="other">Other</button>')
            pw = pg.locator('input[type="password"]').first
            self.assertEqual(w._login_submit(pg, pw).get_attribute("id"), "go")
            pg.set_content('<button id="menu">☰</button><div><input id="u"><input id="p" type="password"></div>'
                           '<button id="other">Other</button><button id="go">Sign in</button>')
            pw = pg.locator('input[type="password"]').first
            self.assertEqual(w._login_submit(pg, pw).get_attribute("id"), "go")
            b.close()


class SetJourneyContractAction(_Scratch):
    def test_validation_and_storage(self):
        sys.modules.pop("repair_actions", None); sys.modules.pop("app_runner", None)
        ra = importlib.import_module("repair_actions")
        with self.assertRaises(ra.ActionError): ra.set_journey_contract("a", "{not json")
        with self.assertRaises(ra.ActionError): ra.set_journey_contract("a", json.dumps({"type": "login", "username": "u", "password": "p"}))
        with self.assertRaises(ra.ActionError): ra.set_journey_contract("a", json.dumps({"type": "smoke", "expected_text": "x"}))
        msg = ra.set_journey_contract("My App", json.dumps({"type": "login", "username": "env:U", "password": "env:P", "expected_text": "Log out"}))
        self.assertIn("watcher decides", msg)
        f = self.root / "state" / "runner" / "recipes" / "my-app.journey.json"
        self.assertTrue(f.is_file())
        self.assertEqual(json.loads(f.read_text())["supplied_by"], "self-healer")


class FleetReport(_Scratch):
    def test_groups_by_code_and_passed_first(self):
        results = [
            {"app": "a", "stages": {"6 CLEAN": {"status": "OK", "journey": {"type": "login", "detail": "ok"}}}},
            {"app": "b", "stages": {"6 CLEAN": {"status": "FAIL", "code": "JOURNEY_NOT_AUTHORED:ACCOUNT_NO_STRATEGY"}},
             "journey_author": {"code": "ACCOUNT_NO_STRATEGY", "account": {"class": None}, "detail": "d"}},
            {"app": "c", "stages": {"6 CLEAN": {"status": "FAIL", "code": "JOURNEY_NOT_AUTHORED:ACCOUNT_NO_STRATEGY"}},
             "journey_author": {"code": "ACCOUNT_NO_STRATEGY", "account": {"class": None}, "detail": "d"}},
            {"app": "d", "stages": {"6 CLEAN": {"status": "FAIL", "code": "JOURNEY_NOT_AUTHORED:SMOKE_NO_CONTROL"}},
             "journey_author": {"code": "SMOKE_NO_CONTROL", "account": {"class": "none"}, "detail": "x"}},
            {"app": "e", "runner": {"started": False}, "broken_at": "2 APP_UP", "stages": {}},
        ]
        text = self.ja.journey_report(results, self.root / "journeys.md")
        self.assertTrue((self.root / "journeys.md").is_file())
        order = [l for l in text.splitlines() if l.startswith("## ")]
        self.assertEqual(order[0], "## PASSED:login  (1)")
        self.assertEqual(order[1], "## ACCOUNT_NO_STRATEGY  (2)")
        self.assertIn("## SMOKE_NO_CONTROL  (1)", order)
        self.assertIn("## NOT_STARTED  (1)", order)


@unittest.skipUnless(os.environ.get("ATTA_LIVE_GOTIFY_URL"), "set ATTA_LIVE_GOTIFY_URL to a running Gotify")
class LiveGotify(_Scratch):
    def test_env_admin_class_end_to_end(self):
        env = {"GOTIFY_DEFAULTUSER_NAME": os.environ["ATTA_LIVE_GOTIFY_USER"], "GOTIFY_DEFAULTUSER_PASS": os.environ["ATTA_LIVE_GOTIFY_PASS"]}
        r = self.ja.author("gotify", os.environ["ATTA_LIVE_GOTIFY_URL"], {"kind": "image", "env": env})
        self.assertIsNone(r["code"], r)
        self.assertEqual(r["account"]["class"], "env_admin")
        self.assertEqual(r["journey"]["username"], "store:gotify")
        self.assertNotIn(env["GOTIFY_DEFAULTUSER_PASS"], json.dumps(r))
        self.assertTrue(any(k in r["journey"] for k in ("expected_text", "expected_selector", "expected_url_contains")))
        # second run: the stored account is reused
        r2 = self.ja.author("gotify", os.environ["ATTA_LIVE_GOTIFY_URL"], {"kind": "image", "env": env})
        self.assertIsNone(r2["code"]); self.assertEqual(r2["account"]["class"], "stored")


@unittest.skipUnless(os.environ.get("ATTA_LIVE_GITEA_URL"), "set ATTA_LIVE_GITEA_URL to a running Gitea")
class LiveGitea(_Scratch):
    def test_signup_class_end_to_end(self):
        r = self.ja.author("gitea", os.environ["ATTA_LIVE_GITEA_URL"], {"kind": "image", "env": {}})
        self.assertIsNone(r["code"], r)
        self.assertEqual(r["account"]["class"], "signup")
        self.assertEqual(r["journey"]["password"], "store:gitea")
        acc = self.ja.load_account("gitea")
        self.assertTrue(acc and acc["username"].startswith("atta-qa"))


@unittest.skipUnless(os.environ.get("ATTA_LIVE_NOLOGIN_URL"), "set ATTA_LIVE_NOLOGIN_URL to a display-only app")
class LiveNoLogin(_Scratch):
    def test_display_only_app_is_reported_not_faked(self):
        r = self.ja.author("glances", os.environ["ATTA_LIVE_NOLOGIN_URL"], {})
        self.assertIsNone(r["journey"])
        self.assertEqual(r["code"], "SMOKE_NO_CONTROL")


if __name__ == "__main__":
    unittest.main()
