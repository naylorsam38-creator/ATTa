"""v117: PR #3's v115 features that the v116 line lacked, ported and checked on this base.

    cd ATTa && python3 -m unittest tests.test_v117_pr3_port -v

  - customer tokens go to Coolify (never read back, never kept, never followed through a redirect) + receipts
  - per-app data belongs to the account that ADDED the app (v116's "named in your build" was everyone, since every
    build re-checks the whole catalogue) — evidence, checklist rows, discovered apps, the build API, tokens
  - cross-site POSTs refused (Sec-Fetch-Site / Origin), login included
  - ATTa's own pages run no script at all (strict CSP); the Front Door keeps its own policy
Real gateway processes, real logins, a fake Coolify that records every request."""
import hashlib, http.server, json, os, re, subprocess, sys, threading, unittest, urllib.error, urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from atta_testlib import DEP, Gateway, tmpdir  # noqa: E402


class FakeCoolify(http.server.BaseHTTPRequestHandler):
    calls = []
    reply = (201, None)
    deploy_redirect = None

    def _body(self):
        n = int(self.headers.get("Content-Length") or 0)
        return self.rfile.read(n) if n else b""

    def _answer(self, code, obj, headers=None):
        b = json.dumps(obj).encode()
        self.send_response(code)
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.send_header("Content-Type", "application/json"); self.send_header("Content-Length", str(len(b)))
        self.end_headers(); self.wfile.write(b)

    def do_PATCH(self):
        body = self._body()
        FakeCoolify.calls.append(("PATCH", self.path, body, self.headers.get("Authorization")))
        code, override = FakeCoolify.reply
        if code in (301, 302, 307, 308):
            return self._answer(code, {}, {"Location": override})
        if override is not None:
            return self._answer(code, override)
        data = json.loads(body)["data"]
        self._answer(201, [{"key": d["key"], "uuid": "e-" + d["key"]} for d in data])   # like a token w/o read:sensitive

    def do_POST(self):
        FakeCoolify.calls.append(("POST", self.path, self._body(), self.headers.get("Authorization")))
        if FakeCoolify.deploy_redirect and "/elsewhere" not in self.path:
            return self._answer(302, {}, {"Location": FakeCoolify.deploy_redirect})
        uuid = self.path.split("uuid=")[1].split("&")[0]
        self._answer(200, {"deployments": [{"resource_uuid": uuid, "deployment_uuid": "d-1"}]})

    def do_GET(self):
        FakeCoolify.calls.append(("GET", self.path, b"", self.headers.get("Authorization")))
        self._answer(200, [{"key": "STRIPE_SECRET_KEY", "value": "must-never-be-asked-for"}])

    def log_message(self, *a):
        pass


def start_fake():
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), FakeCoolify)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def all_text_under(root):
    out = []
    for p in Path(root).rglob("*"):
        if p.is_file():
            try:
                out.append(p.read_text(errors="replace"))
            except OSError:
                pass
    return "\n".join(out)


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None


class PortedGateway(unittest.TestCase):
    CUSTOMER = "sk_live_customerStripe_51Hx9aQ2zz"

    @classmethod
    def setUpClass(cls):
        cls.fake = start_fake()
        cls.t = tmpdir("atta-pr3-")
        cls.gw = Gateway(cls.t)
        root = cls.gw.root
        (root / "library" / "shop").mkdir(parents=True)
        (root / "library" / "otherapp").mkdir(parents=True)
        (root / "coolify_resources.json").write_text(json.dumps({"apps": {"shop": "uuid-shop"}}))
        ev = root / "state" / "runner" / "evidence"
        for app in ("shop", "otherapp"):
            (ev / app).mkdir(parents=True)
            (ev / app / "browser.json").write_text('{"console": []}')
        cls.gw.extra_env = {"COOLIFY_URL": f"http://127.0.0.1:{cls.fake.server_address[1]}",
                            "COOLIFY_TOKEN": "atta-coolify-token-xyz", "APP_BUILDER_TEST_ACCOUNTS": "2"}
        orig_env = cls.gw.env
        cls.gw.env = lambda: {**orig_env(), **cls.gw.extra_env}
        cls.gw.start()
        # TEST_ACCOUNTS.txt lines: "<name> <role> <password>"
        cls.pw = {p[0]: p[2] for p in (l.split() for l in (root / "TEST_ACCOUNTS.txt").read_text().splitlines())
                  if len(p) == 3 and p[1] in ("admin", "user")}
        cls.py(f"""
import app_owners, builds
app_owners.record("shop", "tester01", None)
app_owners.record("otherapp", "tester02", None)
for who in ("tester01", "tester02"):
    b = builds.create(who, origin="web")
    builds.update(b["id"], state="QUALIFIED",
        qualification=[{{"app": "shop", "qualified": True, "evidence": {{"browser": True}}}},
                       {{"app": "otherapp", "qualified": False, "evidence": {{"browser": True}}}}],
        apps_discovered=[{{"name": "shop", "rel": "shop"}}, {{"name": "otherapp", "rel": "otherapp"}}],
        checklist={{"expected": 2, "ticked": 2, "PASS": 1, "FAIL": 1, "NOT_CHECKED": 0, "complete": True,
                   "regressions": ["otherapp"], "rows": [
                   {{"no": "1", "app": "shop", "tick": "PASS", "steps": []}},
                   {{"no": "2", "app": "otherapp", "tick": "FAIL", "steps": []}}]}})
    print(who, b["id"])
""")

    @classmethod
    def py(cls, code):
        r = subprocess.run([sys.executable, "-c", code], env=cls.gw.env(), cwd=str(DEP), capture_output=True,
                           text=True, timeout=60)
        assert r.returncode == 0, r.stderr
        return r.stdout

    @classmethod
    def tearDownClass(cls):
        cls.gw.stop(); cls.fake.shutdown()

    def setUp(self):
        FakeCoolify.calls.clear(); FakeCoolify.reply = (201, None); FakeCoolify.deploy_redirect = None

    def login(self, who):
        body = f"user={who}&password={self.pw[who]}".encode()
        req = urllib.request.Request(self.url("/login"), data=body, method="POST")
        try:
            urllib.request.build_opener(_NoRedirect).open(req)
        except urllib.error.HTTPError as e:
            self.assertEqual(e.code, 302, e.read()[:300])
            return e.headers["Set-Cookie"].split(";")[0]
        self.fail("login did not redirect")

    def url(self, p):
        return f"http://127.0.0.1:{self.gw.port}{p}"

    def req(self, path, who=None, data=None, headers=None, method=None):
        h = dict(headers or {})
        if who:
            h["Cookie"] = self.login(who)
        r = urllib.request.Request(self.url(path), data=data, headers=h, method=method)
        try:
            with urllib.request.build_opener(_NoRedirect).open(r) as resp:
                return resp.status, dict(resp.headers), resp.read()
        except urllib.error.HTTPError as e:
            return e.code, dict(e.headers), e.read()

    # --------------------------------------------------------------------------------------------- ownership
    def test_evidence_belongs_to_whoever_added_the_app(self):
        self.assertEqual(self.req("/evidence/shop/browser.json", "tester01")[0], 200)
        self.assertEqual(self.req("/evidence/otherapp/browser.json", "tester01")[0], 404)   # named in own build
        self.assertEqual(self.req("/evidence/otherapp/browser.json", "tester02")[0], 200)
        self.assertEqual(self.req("/evidence/otherapp/browser.json", "admin")[0], 200)

    def test_build_page_and_api_show_only_your_apps(self):
        code, _, body = self.req("/api/builds", "tester01")
        bid = json.loads(body)[0]["id"]
        code, _, body = self.req(f"/api/builds/{bid}", "tester01")
        r = json.loads(body)
        self.assertEqual([q["app"] for q in r["qualification"]], ["shop"])
        self.assertEqual([a["name"] for a in r["apps_discovered"]], ["shop"])
        cl = r["checklist"]
        self.assertEqual(([x["app"] for x in cl["rows"]], cl["expected"], cl["PASS"], cl["FAIL"], cl["regressions"]),
                         (["shop"], 1, 1, 0, []))                            # counts recomputed: no total leak
        code, _, html = self.req(f"/builds/{bid}", "tester01")
        self.assertEqual(code, 200)
        self.assertNotIn(b"otherapp", html)
        code, _, body = self.req(f"/api/builds/{bid}", "admin")
        self.assertEqual(len(json.loads(body)["qualification"]), 2)          # admins see everything

    # --------------------------------------------------------------------------------------------- tokens
    def test_tokens_page_only_for_owner(self):
        self.assertEqual(self.req("/apps/shop/secrets", "tester01")[0], 200)
        self.assertEqual(self.req("/apps/shop/secrets", "tester02")[0], 404)
        self.assertEqual(self.req("/apps/nosuch/secrets", "admin")[0], 404)

    def test_token_goes_to_coolify_only_and_a_receipt_is_kept(self):
        body = json.dumps({"secrets": {"STRIPE_SECRET_KEY": self.CUSTOMER}}).encode()
        code, _, out = self.req("/api/apps/shop/secrets", "tester01", body, {"Content-Type": "application/json"})
        self.assertEqual(code, 200, out)
        res = json.loads(out)
        self.assertEqual(res["delivered"], ["STRIPE_SECRET_KEY"])
        self.assertNotIn(self.CUSTOMER, out.decode())
        method, path, sent, auth = FakeCoolify.calls[0]
        self.assertEqual((method, path, auth), ("PATCH", "/api/v1/applications/uuid-shop/envs/bulk",
                                                "Bearer atta-coolify-token-xyz"))
        item = json.loads(sent)["data"][0]
        self.assertEqual(item["value"], self.CUSTOMER)
        self.assertTrue(item["is_literal"] and item["is_shown_once"] and item["is_runtime"])
        self.assertFalse(item["is_buildtime"])
        self.assertEqual(FakeCoolify.calls[1][:2], ("POST", "/api/v1/deploy?uuid=uuid-shop&force=false"))
        self.assertNotIn("GET", [c[0] for c in FakeCoolify.calls])           # never reads a value back
        self.assertNotIn(self.CUSTOMER, all_text_under(self.gw.root))        # nowhere on disk
        self.assertNotIn(self.CUSTOMER, (self.t / "gateway.log").read_text())  # nor in the log
        rec = json.loads((self.gw.root / "state/gateway/customer_secrets/shop.json").read_text())["vars"]["STRIPE_SECRET_KEY"]
        self.assertEqual((len(rec["fingerprint"]), rec["by"], rec["delivered_to"]), (16, "tester01", "coolify:application:uuid-shop"))
        self.assertNotIn(rec["fingerprint"], hashlib.sha256(self.CUSTOMER.encode()).hexdigest())   # keyed
        # check: compares fingerprints, never the old token
        chk = lambda v: json.loads(self.req("/api/apps/shop/secrets/check", "tester01",
                                            json.dumps({"name": "STRIPE_SECRET_KEY", "value": v}).encode(),
                                            {"Content-Type": "application/json"})[2])
        self.assertTrue(chk(self.CUSTOMER)["matches"])
        self.assertFalse(chk(self.CUSTOMER + "x")["matches"])

    def test_coolify_redirect_is_not_followed_and_nothing_is_kept(self):
        # v116 rule, kept for the new call: a redirect would carry the Bearer token (and here the customer's
        # values) to another address.
        FakeCoolify.reply = (307, f"http://127.0.0.1:{self.fake.server_address[1]}/elsewhere")
        body = json.dumps({"secrets": {"OPENAI_API_KEY": "sk-proj-customer-owned-1234"}}).encode()
        code, _, out = self.req("/api/apps/shop/secrets", "tester01", body, {"Content-Type": "application/json"})
        self.assertEqual(code, 400)
        self.assertIn("redirect", out.decode())
        self.assertEqual([c[1] for c in FakeCoolify.calls], ["/api/v1/applications/uuid-shop/envs/bulk"])
        self.assertNotIn("sk-proj-customer-owned-1234", all_text_under(self.gw.root))

    def test_redeploy_redirect_never_carries_the_token_elsewhere(self):
        # urllib follows a POST's 302 as a GET and KEEPS the Authorization header: without the no-redirect opener
        # the Coolify token would be sent to whatever address the redirect names.
        FakeCoolify.deploy_redirect = f"http://127.0.0.1:{self.fake.server_address[1]}/elsewhere"
        body = json.dumps({"secrets": {"MAIL_KEY": "customer-mail-key-777"}}).encode()
        code, _, out = self.req("/api/apps/shop/secrets", "tester01", body, {"Content-Type": "application/json"})
        self.assertEqual(code, 200, out)
        self.assertFalse(json.loads(out)["redeploy"]["ok"])
        self.assertEqual([c for c in FakeCoolify.calls if "/elsewhere" in c[1]], [], "followed the redirect")

    def test_coolify_error_echoing_the_value_is_scrubbed(self):
        FakeCoolify.reply = (422, {"message": f"invalid value {self.CUSTOMER}"})
        body = json.dumps({"secrets": {"STRIPE_SECRET_KEY": self.CUSTOMER}}).encode()
        code, _, out = self.req("/api/apps/shop/secrets", "tester01", body, {"Content-Type": "application/json"})
        self.assertEqual(code, 400)
        self.assertNotIn(self.CUSTOMER, out.decode())
        self.assertIn("[customer value hidden]", out.decode())

    def test_refusals_never_echo_values(self):
        for secrets in ({"APP_BUILDER_SESSION_SECRET": "x" * 20}, {"COOLIFY_TOKEN": "v"}, {"LD_PRELOAD": "v"},
                        {"1BAD": "v"}, {"OK_NAME": ""}, {"ANTHROPIC_API_KEY": self.gw.secret}):
            code, _, out = self.req("/api/apps/shop/secrets", "admin", json.dumps({"secrets": secrets}).encode(),
                                    {"Content-Type": "application/json"})
            self.assertEqual(code, 400, secrets)
            self.assertNotIn(self.gw.secret, out.decode())
        self.assertEqual(FakeCoolify.calls, [])

    def test_unmapped_app_sends_nothing(self):
        (self.gw.root / "library" / "unmapped").mkdir(exist_ok=True)
        self.py('import app_owners; app_owners.record("unmapped", "tester01", None)')
        code, _, out = self.req("/api/apps/unmapped/secrets", "tester01",
                                json.dumps({"secrets": {"X_KEY": "customer-value-123"}}).encode(),
                                {"Content-Type": "application/json"})
        self.assertEqual(code, 400)
        self.assertIn("no Coolify resource", out.decode())
        self.assertEqual(FakeCoolify.calls, [])

    # --------------------------------------------------------------------------------------------- cross-site
    def test_cross_site_posts_refused_login_included(self):
        cookie = self.login("tester01")
        for hdrs in ({"Origin": "https://evil.example"}, {"Sec-Fetch-Site": "cross-site"},
                     {"Origin": f"http://127.0.0.1:{self.gw.port + 1}"}, {"Referer": "https://evil.example/x"},
                     {"Origin": "null"}):
            with self.subTest(hdrs=hdrs):
                code, _, _ = self.req("/add-repo", None, b"url=https://github.com/a/b", {**hdrs, "Cookie": cookie})
                self.assertEqual(code, 403)
                code, _, _ = self.req("/login", None, f"user=tester01&password={self.pw['tester01']}".encode(), hdrs)
                self.assertEqual(code, 403)
        same = {"Origin": f"http://127.0.0.1:{self.gw.port}", "Sec-Fetch-Site": "same-origin", "Cookie": cookie}
        code, _, _ = self.req("/api/apps/shop/secrets/check", None, json.dumps({"name": "X", "value": "y"}).encode(),
                              {**same, "Content-Type": "application/json"})
        self.assertEqual(code, 200)

    # --------------------------------------------------------------------------------------------- CSP
    def test_atta_pages_run_no_script(self):
        code, h, _ = self.req("/builds", "tester01")
        csp = h["Content-Security-Policy"]
        self.assertIn("default-src 'none'", csp)
        self.assertNotIn("script-src", csp)
        self.assertEqual(h["X-Frame-Options"], "DENY")
        code, h, _ = self.req("/login")
        self.assertIn("default-src 'none'", h["Content-Security-Policy"])


class Units(unittest.TestCase):
    def test_same_origin_rules(self):
        import importlib
        src = (DEP / "gateway.py").read_text()
        fn = src[src.index("def same_origin_post(h):"):src.index("_RATE_LOCK=")]
        ns = {"urlparse": __import__("urllib.parse").parse.urlparse}
        exec(fn, ns)
        class H:  # noqa
            def __init__(self, **hd): self.headers = hd
        ok = ns["same_origin_post"]
        self.assertTrue(ok(H(Host="atta.example.com")))                                        # curl: no headers
        self.assertTrue(ok(H(Host="atta.example.com", Origin="https://atta.example.com")))
        self.assertTrue(ok(H(Host="127.0.0.1:8787", Origin="http://127.0.0.1:8787")))
        self.assertFalse(ok(H(Host="atta.example.com", Origin="https://app.atta.example.com")))  # sibling app
        self.assertFalse(ok(H(Host="atta.example.com", Origin="https://atta.example.com:8443")))
        self.assertFalse(ok(H(Host="atta.example.com", **{"Sec-Fetch-Site": "same-site"})))


if __name__ == "__main__":
    unittest.main()
