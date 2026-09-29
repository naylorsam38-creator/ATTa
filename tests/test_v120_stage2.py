"""v120: v118 base + the v119 runtime-journey gate, and the Locked Goal's "the system chooses" rule.

Run: python3 -m unittest tests.test_v120_stage2
No Docker or network needed. Uses the real modules against a scratch APP_BUILDER_ROOT.
What is NOT tested here: any catalogue app passing the journey. That evidence only exists after a
real run on the server (state/runner/results/<app>.json with stages["6 CLEAN"].journey).
"""
import importlib, json, os, re, shutil, sys, tempfile, unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
DEP = HERE / "04-deployment"
sys.path.insert(0, str(DEP))


def _result(app, ok=True, stage6="OK", broken=None):
    return {"app": app, "verdict": "PASS" if ok else "FAIL", "broken_at": broken,
            "stages": {"6 CLEAN": {"status": stage6}}}


class SystemChoosesTheFoundation(unittest.TestCase):
    """Locked Goal: the person receives an application, not a library entry, and is never asked to
    choose an underlying application. So for_user() carries ONE foundation per category and no names."""

    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        shutil.copy(HERE / "02-front-door" / "front-door.html", self.root / "front-door.html")
        (self.root / "state" / "runner" / "results").mkdir(parents=True)
        self.uc = importlib.import_module("user_catalogue")

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def write(self, catalogue, results, managed):
        (self.root / "app_catalogue.json").write_text(json.dumps({"apps": catalogue}))
        for aid, r in results.items():
            (self.root / "state/runner/results" / f"{aid}.json").write_text(json.dumps(r))
        (self.root / "coolify_resources.json").write_text(json.dumps({"apps": {}, "managed_by_atta": managed}))

    def test_user_gets_one_foundation_per_category_and_no_names(self):
        self.write({"grafana": {"app": "Grafana", "seed_categories": ["Monitoring"], "status": "MAPPED"},
                    "netdata": {"app": "Netdata", "seed_categories": ["Monitoring"], "status": "MAPPED"}},
                   {"grafana": _result("grafana"), "netdata": _result("netdata")},
                   {"grafana": {"url": "https://g.example.com"}, "netdata": {"url": "https://n.example.com"}})
        u = self.uc.for_user(self.root)
        self.assertEqual(list(u), ["categories"])
        self.assertEqual(len(u["categories"]["monitoring"]), 1)
        chosen = u["categories"]["monitoring"][0]
        self.assertEqual(set(chosen), {"id", "url"})            # no name, no family, nothing else
        self.assertEqual(chosen, {"id": "grafana", "url": "https://g.example.com"})
        # the admin view still sees both candidates with their names
        b = self.uc.build(self.root)
        self.assertEqual([a["name"] for a in b["categories"]["monitoring"]], ["Grafana", "Netdata"])

    def test_choice_is_the_same_every_time(self):
        self.write({"b-app": {"app": "B App", "seed_categories": ["Monitoring"]},
                    "a-app": {"app": "A App", "seed_categories": ["Monitoring"]}},
                   {"b-app": _result("b-app"), "a-app": _result("a-app")},
                   {"b-app": {"url": "https://b"}, "a-app": {"url": "https://a"}})
        ids = {self.uc.for_user(self.root)["categories"]["monitoring"][0]["id"] for _ in range(5)}
        self.assertEqual(ids, {"a-app"})

    def test_chosen_foundation_must_itself_be_qualified_and_live(self):
        # the lowest id is NOT qualified: the choice skips it, it never "wins" by name order
        self.write({"a-app": {"app": "A App", "seed_categories": ["Monitoring"]},
                    "b-app": {"app": "B App", "seed_categories": ["Monitoring"]}},
                   {"a-app": _result("a-app", ok=False, broken="6 CLEAN"), "b-app": _result("b-app")},
                   {"a-app": {"url": "https://a"}, "b-app": {"url": "https://b"}})
        self.assertEqual(self.uc.for_user(self.root)["categories"]["monitoring"][0]["id"], "b-app")

    def test_empty_category_stays_empty(self):
        self.write({"a-app": {"app": "A App", "seed_categories": ["Monitoring"]}},
                   {"a-app": _result("a-app", stage6="FAIL", ok=False, broken="6 CLEAN")}, {"a-app": {"url": "https://a"}})
        self.assertEqual(self.uc.for_user(self.root), {"categories": {}})

    def test_front_door_never_offers_a_pick_or_shows_an_app_name(self):
        html = (HERE / "02-front-door" / "front-door.html").read_text()
        self.assertNotIn("PICK_LINE", html)
        self.assertNotIn("a.name", html)
        self.assertNotIn('el("button", "pill", a', html)
        # the only thing kept about the foundation is its address (and id, for reopening)
        self.assertIn("LIBRARY[r.category_id] = { url: a.url };", html)


class HandOffOnlyWhenQualified(unittest.TestCase):
    def test_partially_qualified_is_not_a_hand_off_state(self):
        builds = importlib.import_module("builds")
        h = importlib.import_module("coolify_handoff")
        self.assertEqual(h.HANDOFF_STATES, (builds.QUALIFIED,))

    def test_pipeline_calls_hand_off_only_for_a_qualified_build(self):
        src = (DEP / "pipeline.py").read_text()
        i = src.index("def _record_qualification")
        body = src[i:src.index("def requalify")]
        self.assertIn("if st == builds.QUALIFIED:", body)
        self.assertLess(body.index("if st == builds.QUALIFIED:"), body.index("coolify_handoff.hand_off"))

    def test_static_snapshot_is_gone_live_catalogue_is_the_only_source(self):
        # v119 wrote state/qualified_catalogue.json as a second, weaker copy of the truth (no live check).
        # v120 has one source: user_catalogue.py, checked fresh on every request.
        self.assertNotIn("qualified_catalogue", (DEP / "pipeline.py").read_text())
        self.assertNotIn("qualified_catalogue", (DEP / "gateway.py").read_text())
        self.assertIn("user_catalogue.for_user", (DEP / "gateway.py").read_text())


class JourneyGateIsWired(unittest.TestCase):
    """The gate exists in the watcher, and the runner carries a recipe's journey to the watcher target.
    (Behaviour of the gate itself: tests/test_v119_login_proof.py, in a real browser.)"""

    def test_watcher_has_the_gate(self):
        w = importlib.import_module("system_watcher")
        for fn in ("_journey_spec", "run_user_journey", "_login_detected", "_credential"):
            self.assertTrue(callable(getattr(w, fn)), fn)
        src = (DEP / "system_watcher.py").read_text()
        # stage 6 runs the journey and FAILS the stage on any journey code
        self.assertIn("journey_code, journey_detail = run_user_journey(page, journey, url)", src)
        self.assertIn("return stage('FAIL', journey_code, journey_detail)", src)
        self.assertIn("journey=_journey_spec(t,app)", src)

    def test_missing_journey_is_a_fail_code(self):
        w = importlib.import_module("system_watcher")
        code, detail = w.run_user_journey(None, None, "http://x")
        self.assertEqual(code, "JOURNEY_NOT_CONFIGURED")
        code, detail = w.run_user_journey(None, {"type": "login", "enabled": False}, "http://x")
        self.assertEqual(code, "JOURNEY_NOT_CONFIGURED")
        code, detail = w.run_user_journey(None, {"type": "magic"}, "http://x")
        self.assertEqual(code, "JOURNEY_TYPE_UNSUPPORTED")

    def test_login_contract_needs_account_and_expectation_before_touching_the_page(self):
        w = importlib.import_module("system_watcher")
        # page=None: if either check were skipped this would raise instead of returning a code
        self.assertEqual(w.run_user_journey(None, {"type": "login"}, "u")[0], "TEST_ACCOUNT_MISSING")
        self.assertEqual(w.run_user_journey(None, {"type": "login", "username": "a", "password": "b"}, "u")[0],
                         "LOGIN_EXPECTATION_MISSING")

    def test_env_credentials_are_read_from_the_environment(self):
        w = importlib.import_module("system_watcher")
        os.environ["ATTA_T_USER"] = "qa"
        try:
            self.assertEqual(w._credential({"username": "env:ATTA_T_USER"}, "username"), "qa")
            self.assertEqual(w._credential({"username": "env:ATTA_T_MISSING"}, "username"), "")
            self.assertEqual(w._credential({"username": "literal"}, "username"), "literal")
        finally:
            del os.environ["ATTA_T_USER"]

    def test_runner_keeps_journey_in_the_recipe_and_hands_it_to_the_watcher(self):
        src = (DEP / "app_runner.py").read_text()
        self.assertIn('"build", "journey", "account") if k in part', src)
        self.assertIn('t["journey"] = run["part"]["journey"]', src)

    def test_release_says_v120_and_carries_v118(self):
        rel = json.loads((HERE / "release.json").read_text())
        self.assertEqual(rel["version"], "v121.2")
        self.assertTrue(any(f.startswith("v118_") for f in rel["features"]))
        self.assertTrue(any(f.startswith("v120_") for f in rel["features"]))


if __name__ == "__main__":
    unittest.main()
