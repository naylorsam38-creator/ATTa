"""v118 merge: the catalogue guarantee, no guessed repos, runner gates, model name, HTTPS route.

Run: python3 -m unittest discover -s tests
No Docker, network or root needed. Uses real modules against a scratch APP_BUILDER_ROOT.
"""
import importlib, json, os, re, shutil, subprocess, sys, tempfile, unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
DEP = HERE / "04-deployment"
sys.path.insert(0, str(DEP))


def _result(app, ok=True, stage6="OK", broken=None):
    return {"app": app, "verdict": "PASS" if ok else "FAIL", "broken_at": broken,
            "stages": {"6 CLEAN": {"status": stage6}}}


class CatalogueGuarantee(unittest.TestCase):
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

    def test_only_qualified_live_apps_are_shown(self):
        self.write(
            {"grafana": {"app": "Grafana", "seed_categories": ["Monitoring"], "status": "MAPPED"},
             "umami": {"app": "Umami", "seed_categories": ["Analytics"], "status": "MAPPED"},
             "plane": {"app": "Plane", "seed_categories": ["Collaboration"], "status": "MAPPED"},
             "memos": {"app": "Memos", "seed_categories": ["Note Taking"], "status": "MAPPED"},
             "codex": {"app": "Codex", "seed_categories": ["AI Code Assistants"], "status": "BLOCKED"}},
            {"grafana": _result("grafana"),
             "umami": _result("umami", ok=False, broken="2 APP_UP"),         # failed its latest check
             "plane": _result("plane", stage6="NOT_RUN"),                     # browser stage didn't pass
             "memos": _result("memos"),                                       # passed, but not live
             "codex": _result("codex")},
            {"grafana": {"url": "grafana.example.com"}, "umami": {"url": "https://u.example.com"},
             "plane": {"url": "https://p.example.com"}})
        b = self.uc.build(self.root)
        self.assertEqual([a["id"] for a in b["apps"]], ["grafana"])
        self.assertEqual(b["categories"], {"monitoring": [{"id": "grafana", "name": "Grafana",
                                                           "url": "https://grafana.example.com", "family": ""}]})
        self.assertIn("failed", b["excluded"]["umami"])
        self.assertIn("browser stage 6", b["excluded"]["plane"])
        self.assertIn("not live", b["excluded"]["memos"])
        self.assertIn("BLOCKED", b["excluded"]["codex"])

    def test_user_view_carries_no_failures(self):
        self.write({"umami": {"app": "Umami", "seed_categories": ["Analytics"]}},
                   {"umami": _result("umami", ok=False, broken="6 CLEAN")}, {"umami": {"url": "https://u"}})
        self.assertEqual(self.uc.for_user(self.root), {"categories": {}})

    def test_app_drops_out_when_it_stops_qualifying_and_returns(self):
        cat = {"grafana": {"app": "Grafana", "seed_categories": ["Monitoring"]}}
        live = {"grafana": {"url": "https://g"}}
        self.write(cat, {"grafana": _result("grafana")}, live)
        self.assertIn("monitoring", self.uc.for_user(self.root)["categories"])
        self.write(cat, {"grafana": _result("grafana", ok=False, broken="3 PROXY_UP")}, live)
        self.assertEqual(self.uc.for_user(self.root)["categories"], {})
        self.write(cat, {"grafana": _result("grafana")}, live)
        self.assertIn("monitoring", self.uc.for_user(self.root)["categories"])

    def test_every_seed_category_reaches_a_front_door_category(self):
        cats = self.uc.front_door_categories(self.root)
        self.assertEqual(len(cats), 52)
        seeds = {e["category"] for e in json.loads((DEP / "upstream_apps.json").read_text())["entries"]}
        missing = [s for s in seeds if not self.uc.categories_for({"seed_categories": [s]}, cats)]
        self.assertEqual(missing, [])

    def test_hand_set_category_wins(self):
        cats = self.uc.front_door_categories(self.root)
        e = {"seed_categories": ["Monitoring"], "frontdoor_categories": ["devops", "not-a-category"]}
        self.assertEqual(self.uc.categories_for(e, cats), ["devops"])


class NoGuessedRepos(unittest.TestCase):
    def test_only_verified_entries_are_cloned(self):
        root = Path(tempfile.mkdtemp())
        try:
            (root / "state").mkdir(); (root / "library").mkdir()
            entries = [
                {"app": "Codex", "repository": "OPENAI-CODEX-NEEDS-EXACT-REPO", "status": "NEEDS_EXACT_IDENTIFICATION"},
                {"app": "OpenReel", "repository": "OPENREEL-NEEDS-EXACT-REPO", "status": "NEEDS_EXACT_IDENTIFICATION"},
                {"app": "FormBee", "repository": "FormBee/FormBee", "status": "VERIFIED_CANDIDATE"},
                {"app": "—", "repository": "—", "status": "NEEDS_EXACT_IDENTIFICATION"},
            ]
            (root / "upstream_apps.json").write_text(json.dumps({"entries": entries}))
            code = ("import pipeline, json; pipeline.run=lambda *a,**k: (_ for _ in ()).throw(AssertionError('clone attempted'));"
                    "print(json.dumps([x['result'] for x in pipeline.library()]))")
            env = {**os.environ, "APP_BUILDER_ROOT": str(root), "APP_BUILDER_SESSION_SECRET": "x"}
            out = subprocess.run([sys.executable, "-c", code], cwd=DEP, env=env, capture_output=True, text=True, timeout=60)
            self.assertEqual(out.returncode, 0, out.stderr[-2000:])
            results = json.loads(out.stdout.strip().splitlines()[-1])
            self.assertEqual(set(results), {"BLOCKED_NEEDS_REPO"})
            self.assertEqual(list((root / "library").iterdir()), [])
        finally:
            shutil.rmtree(root, ignore_errors=True)


class RunnerAndHealer(unittest.TestCase):
    def test_runner_keeps_start_gates_and_all_at_once_default(self):
        src = (DEP / "app_runner.py").read_text()
        self.assertIn('"APP_BUILDER_RUN_PARALLEL", "0"', src)
        self.assertIn("def _wait_for_memory(", src)
        self.assertIn("_DOWNLOAD_SLOTS", src)
        self.assertIn("parallel or PARALLEL or len(unique)", src)   # v121.2: names deduplicated by identity first
        # Never a global "-a" image prune: live Coolify apps' images share this Docker.
        self.assertIsNone(re.search(r'"image", "prune", "-af"|"-f" if len\(_RUNNING\) > 1 else "-af"', src))

    def test_heal_model_is_a_real_model_id(self):
        self.assertIn('"APP_BUILDER_HEAL_MODEL", "claude-opus-5-5"', (DEP / "llm_repair.py").read_text())


class HttpsRoute(unittest.TestCase):
    def test_deploy_script_writes_a_valid_traefik_route(self):
        s = (HERE / "05-coolify/kit/scripts/deploy-atta.sh").read_text()
        block = re.search(r'(cat >"\$tmp" <<ROUTE\n.*?\nROUTE\n)', s, re.S).group(1)
        d = Path(tempfile.mkdtemp())
        try:
            sh = f'ATTA_DOMAIN=atta.example.com; ATTA_PORT=8787; tmp="{d}/atta.yaml"\n' + block
            subprocess.run(["bash", "-c", sh], check=True)
            y = (d / "atta.yaml").read_text()
            self.assertIn('rule: "Host(`atta.example.com`)"', y)
            self.assertIn("certResolver: letsencrypt", y)
            self.assertIn('url: "http://host.docker.internal:8787"', y)
            self.assertIn("scheme: https", y)
        finally:
            shutil.rmtree(d, ignore_errors=True)
        self.assertEqual(subprocess.run(["bash", "-n", str(HERE / "05-coolify/kit/scripts/deploy-atta.sh")]).returncode, 0)


if __name__ == "__main__":
    unittest.main()
