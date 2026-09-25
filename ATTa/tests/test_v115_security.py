"""v115 security tests: every test is an attempt to break one of the fixes.

    cd ATTa && python3 -m unittest discover -s tests -v

  #1  an uploaded app's compose file tries to get ATTa's own secrets (real `docker compose config` when the
      Compose CLI is installed; no Docker daemon needed), and customer tokens go only to Coolify (a fake
      Coolify server records every request).
  #2  stage-6 evidence (the page an uploaded app produced) tries to run inside ATTa, or be read by another
      account (a real gateway process on a free port).
  #3  jobs try to borrow trust from a reserved name ("system", "deployctl", "incoming") or from a missing
      origin, in the pipeline and in ADM.
  +   cross-site POSTs to the gateway.
No root, no network beyond 127.0.0.1."""
import hashlib, http.server, importlib, io, json, os, shutil, socket, stat, subprocess, sys, tempfile, threading, time
import unittest, urllib.error, urllib.request, zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
DEP = HERE.parent / "04-deployment"
TMP = Path(tempfile.mkdtemp(prefix="atta-v115-"))
os.environ.setdefault("APP_BUILDER_ROOT", str(TMP / "root"))
os.environ.setdefault("ATTA_ADM_ROOT", str(TMP / "adm"))
sys.path[:0] = [str(DEP), str(DEP / "deployd")]

import accounts, builds, pipeline, app_runner, app_env, app_owners, customer_secrets, coolify_handoff, reserved  # noqa: E402
from adm import config as adm_config, authz, manager, queue as adm_queue  # noqa: E402

ROOT = builds.ROOT   # whichever test module imported first decided it; use the modules' own view
HAVE_COMPOSE = shutil.which("docker") is not None and subprocess.run(
    ["docker", "compose", "version"], capture_output=True).returncode == 0

ATTA_SECRET = "atta-session-secret-9f8e7d6c5b4a"
ATTA_MODEL_KEY = "sk-ant-atta-own-key-0123456789"


def make_zip(path, files):
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        for name, data in files.items():
            z.writestr(name, data)
    return path


def users(**roles):
    d = {"users": {}}
    for name, r in roles.items():
        role, disabled = (r, False) if isinstance(r, str) else r
        d["users"][name] = {"name": name, "role": role, "disabled": disabled, "session_version": 1,
                            "password": accounts.make_hash("pw-" + name)}
    accounts.USERS_FILE.parent.mkdir(parents=True, exist_ok=True)
    accounts.USERS_FILE.write_text(json.dumps(d))


def all_text_under(root: Path) -> str:
    out = []
    for p in root.rglob("*"):
        if p.is_file() and not p.is_symlink():
            try:
                out.append(p.read_bytes().decode("utf-8", "replace"))
            except OSError:
                pass
    return "\n".join(out)


# ============================================================================ #3 reserved names + origin
class ReservedNamesAreNotAccounts(unittest.TestCase):
    def setUp(self):
        users(admin="admin", tester01="user", system="admin", deployctl="admin")

    def test_reserved_names_cannot_be_created(self):
        for name in ("system", "deployctl", "incoming", "local", "root"):
            with self.assertRaisesRegex(ValueError, "reserved"):
                accounts.create(name, "admin", "whatever-password")

    def test_existing_reserved_account_is_dead(self):
        # Someone made a "system" admin before v115. It must not log in or count as anyone.
        self.assertIsNone(accounts.get("system"))
        self.assertIsNone(accounts.verify("system", "pw-system"))
        self.assertEqual(sorted(accounts.disable_reserved()), ["deployctl", "system"])
        raw = json.loads(accounts.USERS_FILE.read_text())["users"]
        self.assertTrue(raw["system"]["disabled"] and raw["deployctl"]["disabled"])
        self.assertFalse(raw["admin"].get("disabled"))
        with self.assertRaisesRegex(ValueError, "reserved"):
            accounts.update("system", disabled=False)

    def test_admin_name_env_cannot_be_reserved(self):
        saved = accounts.ADMIN_NAME
        try:
            accounts.ADMIN_NAME = "system"
            with self.assertRaisesRegex(ValueError, "reserved"):
                accounts.init()
        finally:
            accounts.ADMIN_NAME = saved

    def test_build_needs_an_origin(self):
        with self.assertRaises(TypeError):
            builds.create("admin")                      # no origin at all
        with self.assertRaisesRegex(ValueError, "origin"):
            builds.create("admin", origin="system")
        self.assertEqual(builds.create("admin", origin="web")["origin"], "web")

    def test_adm_uses_the_same_reserved_list(self):
        self.assertIsNotNone(authz._reserved())
        self.assertEqual(authz._reserved().RESERVED_NAMES, reserved.RESERVED_NAMES)


class PipelineTrustsOriginNotNames(unittest.TestCase):
    def setUp(self):
        users(admin="admin", tester01="user", system="admin")
        pipeline.PKG.mkdir(parents=True, exist_ok=True)
        (pipeline.PKG / "marker").write_text("original package")
        (pipeline.ROOT / "front-door.html").write_text("original front door")

    def _bundle(self, dest: Path):
        inner = io.BytesIO()
        with zipfile.ZipFile(inner, "w") as z:
            z.writestr("out/install_all.py", "raise SystemExit('must never run')\n")
        dest.parent.mkdir(parents=True, exist_ok=True)
        return make_zip(dest, {"ATTa/03-ui-skins-capability-package/UI_Skin_Capability_OneShot_v2.zip": inner.getvalue(),
                               "ATTa/02-front-door/front-door.html": "<script>replaced()</script>"})

    def assertUntouched(self):
        self.assertEqual((pipeline.PKG / "marker").read_text(), "original package")
        self.assertEqual((pipeline.ROOT / "front-door.html").read_text(), "original front door")

    def test_inbox_item_without_record_is_not_system(self):
        # Before v115 this became a 'system' build and was trusted to update the server.
        before = {r["id"] for r in builds.all_builds()}
        b = self._bundle(pipeline.INBOX / "dropped.zip")
        self.assertEqual(pipeline.process(b), builds.FAILED)
        self.assertEqual({r["id"] for r in builds.all_builds()}, before)   # no build invented for it
        self.assertUntouched()

    def test_record_without_origin_is_refused(self):
        rec = builds.create("admin", origin="web")
        raw = json.loads(builds.path(rec["id"]).read_text()); raw.pop("origin")
        builds.path(rec["id"]).write_text(json.dumps(raw))                 # a record from before v115
        b = self._bundle(pipeline.INBOX / f"{rec['id']}.zip")
        self.assertEqual(pipeline.process(b), builds.FAILED)
        self.assertTrue(builds.get(rec["id"])["refused"])
        self.assertUntouched()

    def test_web_record_naming_system_is_refused(self):
        rec = builds.create("system", origin="web")                        # forged or pre-v115 account
        b = self._bundle(pipeline.INBOX / f"{rec['id']}.zip")
        self.assertEqual(pipeline.process(b), builds.FAILED)
        self.assertIn("not a web upload by an account", builds.get(rec["id"])["error"])
        self.assertUntouched()

    def test_local_origin_needs_the_local_check(self):
        self.assertFalse(pipeline.may_update_system({"origin": "local", "owner": "system"}))
        self.assertFalse(pipeline.may_update_system({"origin": "local", "owner": "admin", "local_verified": True}))
        self.assertFalse(pipeline.may_update_system({"origin": "", "owner": "admin"}))
        self.assertFalse(pipeline.may_update_system({"owner": "admin"}))
        # the APP_BUILDER_AUTH_DISABLED test identity adds apps but never updates the system
        self.assertFalse(pipeline.may_update_system({"origin": "web", "owner": "local-admin"}))
        self.assertFalse(reserved.is_reserved("local-admin"))

    def test_local_inbox_rejects_loose_files(self):
        pipeline.LOCAL_INBOX.mkdir(mode=0o700, exist_ok=True)
        os.chmod(pipeline.LOCAL_INBOX, 0o700)
        good = pipeline.LOCAL_INBOX / "ok.zip"; good.write_bytes(b"x"); os.chmod(good, 0o600)
        self.assertTrue(pipeline.local_item_trusted(good)[0])
        loose = pipeline.LOCAL_INBOX / "loose.zip"; loose.write_bytes(b"x"); os.chmod(loose, 0o666)
        self.assertFalse(pipeline.local_item_trusted(loose)[0])
        link = pipeline.LOCAL_INBOX / "link.zip"; link.symlink_to(good)
        self.assertFalse(pipeline.local_item_trusted(link)[0])
        os.chmod(pipeline.LOCAL_INBOX, 0o777)
        try:
            self.assertFalse(pipeline.local_item_trusted(good)[0])
        finally:
            os.chmod(pipeline.LOCAL_INBOX, 0o700)
            for p in (good, loose, link):
                p.unlink()

    def test_untrusted_local_bundle_changes_nothing(self):
        pipeline.LOCAL_INBOX.mkdir(mode=0o700, exist_ok=True)
        b = self._bundle(pipeline.LOCAL_INBOX / "loose-bundle.zip"); os.chmod(b, 0o666)
        try:
            self.assertEqual(pipeline.process(b), builds.FAILED)
            self.assertUntouched()
            rec = max(builds.all_builds(), key=lambda r: r["created"])
            self.assertEqual((rec["origin"], rec["owner"], rec.get("local_verified")), ("local", "system", False))
            self.assertIn("writable by group/others", rec["error"])
        finally:
            b.unlink(missing_ok=True)


class AdmTrustsOriginNotNames(unittest.TestCase):
    def setUp(self):
        authz.USERS_FILE = accounts.USERS_FILE
        users(admin="admin", tester01="user", system="admin")
        adm_config.ensure_dirs()

    def tearDown(self):
        for m in adm_queue.pending():
            adm_queue.finish(m)

    def _job(self, **meta_changes):
        z = make_zip(TMP / "j.zip", {"x.txt": "hi"})
        job = adm_queue.enqueue(z, requested_by="admin", origin="web")
        mp = adm_config.QUEUE / f"{job}.json"
        meta = json.loads(mp.read_text())
        for k, v in meta_changes.items():
            if v is None:
                meta.pop(k, None)
            else:
                meta[k] = v
        mp.write_text(json.dumps(meta))
        return meta

    def test_enqueue_requires_origin_and_requester(self):
        z = make_zip(TMP / "k.zip", {"x.txt": "hi"})
        with self.assertRaises(ValueError):
            adm_queue.enqueue(z, requested_by="admin")
        with self.assertRaises(ValueError):
            adm_queue.enqueue(z, origin="web")

    def test_missing_or_unknown_origin(self):
        self.assertFalse(authz.allowed(self._job(origin=None))[0])
        self.assertFalse(authz.allowed(self._job(origin="system"))[0])
        self.assertTrue(authz.allowed(self._job())[0])

    def test_web_job_naming_system_even_if_an_admin_account_has_that_name(self):
        ok, why = authz.allowed(self._job(requested_by="system"))
        self.assertFalse(ok); self.assertIn("reserved", why)
        for who in ("deployctl", "incoming"):
            self.assertFalse(authz.allowed(self._job(requested_by=who))[0])

    def test_local_job_naming_a_person(self):
        self.assertFalse(authz.allowed(self._job(origin="local", requested_by="admin"))[0])

    def test_loosened_queue_refuses_everything(self):
        meta = self._job(origin="local", requested_by="deployctl")
        mp = adm_config.QUEUE / f"{meta['job_id']}.json"
        os.chmod(mp, 0o666)
        self.assertFalse(authz.allowed(meta)[0])
        os.chmod(mp, 0o600); os.chmod(adm_config.QUEUE, 0o777)
        try:
            ok, why = authz.allowed(meta)
            self.assertFalse(ok); self.assertIn("writable by group/others", why)
        finally:
            os.chmod(adm_config.QUEUE, 0o700)

    def test_reserved_web_job_never_backs_up_or_activates(self):
        meta = self._job(requested_by="system")
        self.assertEqual(manager.process(meta), "FAILED")
        j = json.loads((adm_config.JOURNAL / f"{meta['job_id']}.json").read_text())
        self.assertEqual([e["event"] for e in j["events"]][-1], "FAILED")
        self.assertNotIn("BACKED_UP", [e["event"] for e in j["events"]])

    def test_incoming_must_be_protected(self):
        z = adm_config.INCOMING / "drop.zip"; make_zip(z, {"x": "y"}); os.chmod(z, 0o666)
        self.assertEqual(adm_queue.sweep_incoming(), [])
        self.assertFalse(z.exists())
        self.assertTrue(any((adm_config.INCOMING / "rejected").glob("*drop.zip")))
        target = TMP / "elsewhere.zip"; make_zip(target, {"x": "y"})
        (adm_config.INCOMING / "link.zip").symlink_to(target)
        self.assertEqual(adm_queue.sweep_incoming(), [])
        good = adm_config.INCOMING / "good.zip"; make_zip(good, {"x": "y"}); os.chmod(good, 0o600)
        jobs = adm_queue.sweep_incoming()
        self.assertEqual(len(jobs), 1)
        meta = [m for m in adm_queue.pending() if m["job_id"] == jobs[0]][0]
        self.assertEqual((meta["origin"], meta["requested_by"]), ("local", "incoming"))
        self.assertTrue(authz.allowed(meta)[0])


class InstallerKeepsTrustFoldersPrivate(unittest.TestCase):
    def test_secure_state_on_every_run(self):
        root = TMP / "inst"; (root / "state/customer_secrets").mkdir(parents=True, exist_ok=True)
        (root / "local-inbox").mkdir(exist_ok=True); os.chmod(root / "local-inbox", 0o777)
        (root / "state/secret-fingerprint.key").write_text("k" * 64); os.chmod(root / "state/secret-fingerprint.key", 0o644)
        (root / "state/customer_secrets/shop.json").write_text("{}"); os.chmod(root / "state/customer_secrets/shop.json", 0o666)
        r = subprocess.run(["bash", "-c", f'. "{DEP}/bootstrap-lib.sh"; atta_secure_state "{root}"'], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        mode = lambda p: oct((root / p).stat().st_mode & 0o777)
        self.assertEqual([mode("local-inbox"), mode("state/customer_secrets"), mode("state/secret-fingerprint.key"),
                          mode("state/customer_secrets/shop.json")], ["0o700", "0o700", "0o600", "0o600"])


# ============================================================================ #1 ATTa secrets vs app compose
class RunnerEnvironment(unittest.TestCase):
    def setUp(self):
        self.saved = dict(os.environ)
        os.environ["APP_BUILDER_SESSION_SECRET"] = ATTA_SECRET
        os.environ["ANTHROPIC_API_KEY"] = ATTA_MODEL_KEY
        os.environ["COOLIFY_TOKEN"] = "coolify-token-of-atta-777"

    def tearDown(self):
        os.environ.clear(); os.environ.update(self.saved)

    def test_runner_env_has_no_atta_secrets(self):
        env = app_runner._env_for({"env": {"DB_PASSWORD": "app-own"}}, "someapp")
        for bad in ("APP_BUILDER_SESSION_SECRET", "ANTHROPIC_API_KEY", "COOLIFY_TOKEN", "HTTPS_PROXY", "https_proxy"):
            self.assertNotIn(bad, env)
        self.assertEqual(env["DB_PASSWORD"], "app-own")
        self.assertIn("PATH", env)

    def test_every_docker_call_is_scrubbed(self):
        seen = []
        real = subprocess.run
        def fake(cmd, **kw):
            seen.append((cmd, kw.get("env")))
            return subprocess.CompletedProcess(cmd, 0, "", "")
        app_runner.subprocess.run = fake
        try:
            app_runner.sh(["docker", "build", "-t", "x", "."])
            app_runner.sh(["docker", "compose", "up"])
            app_runner.sh(["git", "status"])
        finally:
            app_runner.subprocess.run = real
        for cmd, env in seen[:2]:
            self.assertIsNotNone(env, cmd)
            self.assertNotIn(ATTA_SECRET, json.dumps(env))
        self.assertIsNone(seen[2][1])   # non-docker helpers are unchanged

    def test_protected_value_detection(self):
        vals = app_env.protected_values()
        self.assertIn(ATTA_SECRET, vals)
        self.assertEqual(app_env.find_protected({"a": ["x", {"b": "pre" + ATTA_MODEL_KEY}]}, vals),
                         ["a[1].b holds ATTa's ANTHROPIC_API_KEY"])
        self.assertEqual(app_env.find_protected({"image": "nginx", "port": "8787"}, vals), [])

    def test_placeholders_never_the_real_value(self):
        d = {"app": "shop", "vars": {"STRIPE_SECRET_KEY": {"fingerprint": "abc"}}, "history": []}
        customer_secrets._save("shop", d)
        env = app_runner._env_for({"env": {}}, "shop")
        self.assertEqual(env["STRIPE_SECRET_KEY"], app_env.PLACEHOLDER)
        env = app_runner._env_for({"env": {"STRIPE_SECRET_KEY": "runner-choice"}}, "shop")
        self.assertEqual(env["STRIPE_SECRET_KEY"], "runner-choice")


@unittest.skipUnless(HAVE_COMPOSE, "docker compose CLI not installed")
class ComposeCannotReachAttaSecrets(unittest.TestCase):
    """Real `docker compose config` (no daemon needed): exactly what the runner renders before `up`."""

    def setUp(self):
        self.saved = dict(os.environ)
        os.environ["APP_BUILDER_SESSION_SECRET"] = ATTA_SECRET
        os.environ["ANTHROPIC_API_KEY"] = ATTA_MODEL_KEY
        (ROOT / ".env").parent.mkdir(parents=True, exist_ok=True)
        (ROOT / ".env").write_text(f"APP_BUILDER_SESSION_SECRET={ATTA_SECRET}\nANTHROPIC_API_KEY={ATTA_MODEL_KEY}\n")
        self.app = f"evil{time.time_ns() % 10**8}"
        self.d = app_runner.LIB / self.app
        self.d.mkdir(parents=True)
        self.saved_harden = (app_runner.HARDEN, app_runner.ALLOW_HOST_ACCESS)

    def tearDown(self):
        os.environ.clear(); os.environ.update(self.saved)
        app_runner.HARDEN, app_runner.ALLOW_HOST_ACCESS = self.saved_harden
        shutil.rmtree(self.d, ignore_errors=True)
        shutil.rmtree(app_runner.WORK / app_runner.safe_id(self.app), ignore_errors=True)

    def render(self, compose: str):
        (self.d / "docker-compose.yml").write_text(compose)
        notes = []
        ok, err = app_runner._render_compose(self.app, self.d, {"kind": "compose", "file": "docker-compose.yml", "env": {}}, notes)
        out = app_runner.WORK / app_runner.safe_id(self.app) / "compose.rendered.json"
        return ok, err, (out.read_text() if ok else ""), notes

    def assertNoSecret(self, text):
        self.assertNotIn(ATTA_SECRET, text)
        self.assertNotIn(ATTA_MODEL_KEY, text)

    def test_interpolation_and_passthrough_get_nothing(self):
        ok, err, out, _ = self.render(
            "services:\n  web:\n    image: nginx:1.27-alpine\n    environment:\n"
            "      - LEAK=${APP_BUILDER_SESSION_SECRET}\n      - ANTHROPIC_API_KEY\n      - ALSO=$ANTHROPIC_API_KEY\n"
            "    build:\n      context: .\n      args:\n        - APP_BUILDER_SESSION_SECRET\n")
        self.assertTrue(ok, err)
        self.assertNoSecret(out)

    def test_env_file_pointing_at_atta(self):
        ok, err, out, _ = self.render(
            f"services:\n  web:\n    image: nginx:1.27-alpine\n    env_file: {ROOT / '.env'}\n")
        self.assertFalse(ok); self.assertIn("__UNSAFE_CONFIG__", err); self.assertIn("env_file", err)
        self.assertNoSecret(err)

    def test_env_file_escaping_by_dots(self):
        ok, err, _, _ = self.render("services:\n  web:\n    image: nginx:1.27-alpine\n    env_file: ../../.env\n")
        self.assertFalse(ok); self.assertIn("outside the app's folder", err)

    def test_symlinked_dotenv(self):
        (self.d / ".env").symlink_to(ROOT / ".env")
        ok, err, _, _ = self.render("services:\n  web:\n    image: nginx:1.27-alpine\n    environment:\n      - X=${ANTHROPIC_API_KEY}\n")
        self.assertFalse(ok); self.assertIn(".env", err)
        self.assertNoSecret(err)

    def test_symlinked_example_is_not_copied(self):
        (self.d / ".env.example").symlink_to(ROOT / ".env")
        ok, err, out, notes = self.render("services:\n  web:\n    image: nginx:1.27-alpine\n    env_file: .env\n")
        self.assertNoSecret(out + err + json.dumps(notes))
        if (self.d / ".env").exists():
            self.assertNoSecret((self.d / ".env").read_text())

    def test_app_file_holding_an_atta_value_is_caught(self):
        (self.d / "app.env").write_text(f"TOKEN={ATTA_MODEL_KEY}\n")   # copied in by some other route
        ok, err, _, _ = self.render("services:\n  web:\n    image: nginx:1.27-alpine\n    env_file: app.env\n")
        self.assertFalse(ok); self.assertIn("holds ATTa's ANTHROPIC_API_KEY", err)
        self.assertNoSecret(err)

    def test_secret_and_config_files_outside(self):
        ok, err, _, _ = self.render(
            "services:\n  web:\n    image: nginx:1.27-alpine\n    secrets: [s]\n"
            f"secrets:\n  s:\n    file: {ROOT / '.env'}\n")
        self.assertFalse(ok); self.assertIn("secrets.s file", err)

    def test_named_volume_binding_the_host(self):
        ok, err, _, _ = self.render(
            "services:\n  web:\n    image: nginx:1.27-alpine\n    volumes: [hostroot:/h]\n"
            "volumes:\n  hostroot:\n    driver: local\n    driver_opts: {type: none, o: bind, device: /}\n")
        self.assertFalse(ok); self.assertIn("device", err)

    def test_build_context_outside(self):
        ok, err, _, _ = self.render(f"services:\n  web:\n    build:\n      context: {ROOT}\n")
        self.assertFalse(ok); self.assertIn("build context", err)

    def test_compose_file_symlinked_out(self):
        outside = TMP / f"outside-{self.app}.yml"
        outside.write_text("services:\n  web:\n    image: nginx:1.27-alpine\n")
        (self.d / "docker-compose.yml").symlink_to(outside)
        ok, err = app_runner._render_compose(self.app, self.d, {"kind": "compose", "file": "docker-compose.yml", "env": {}}, [])
        self.assertFalse(ok); self.assertIn("compose file itself", err)

    def test_include_outside(self):
        other = TMP / f"other-{self.app}.yml"
        other.write_text(f"services:\n  x:\n    image: nginx:1.27-alpine\n    env_file: {ROOT / '.env'}\n")
        ok, err, _, _ = self.render(f"include:\n  - {other}\nservices:\n  web:\n    image: nginx:1.27-alpine\n")
        self.assertFalse(ok); self.assertIn("include", err)

    def test_atta_data_never_mounted_even_with_host_access(self):
        app_runner.ALLOW_HOST_ACCESS = True
        ok, err, out, notes = self.render(
            "services:\n  web:\n    image: portainer/portainer-ce\n    volumes:\n"
            f"      - {ROOT}:/atta\n      - /:/host\n      - /var/run/docker.sock:/var/run/docker.sock\n")
        self.assertTrue(ok, err)
        vols = [v.get("source") for v in json.loads(out)["services"]["web"].get("volumes", [])]
        self.assertNotIn(str(ROOT), vols)
        self.assertNotIn("/", vols)
        self.assertIn("/var/run/docker.sock", vols)   # the opt-in still works for Docker managers

    def test_other_apps_work_folder_not_mountable(self):
        other = app_runner.WORK / "someoneelse" / "data"; other.mkdir(parents=True, exist_ok=True)
        ok, err, out, notes = self.render(f"services:\n  web:\n    image: nginx:1.27-alpine\n    volumes:\n      - {other}:/steal\n")
        self.assertTrue(ok, err)
        self.assertNotIn(str(other), out)

    def test_ordinary_app_still_renders(self):
        (self.d / ".env.example").write_text("DB_PASSWORD=\nGREETING=hello\n")
        ok, err, out, notes = self.render(
            "services:\n  web:\n    image: nginx:1.27-alpine\n    ports: ['8080:80']\n    env_file: .env\n"
            "    environment:\n      - PW=${DB_PASSWORD}\n    volumes: ['./data:/data']\n")
        self.assertTrue(ok, err)
        cfg = json.loads(out)["services"]["web"]
        self.assertEqual(cfg["environment"]["GREETING"], "hello")
        self.assertTrue(cfg["environment"]["PW"])            # runner generated it: the app still boots
        self.assertEqual(cfg["ports"][0]["host_ip"], "127.0.0.1")


# ============================================================================ #1 customer tokens -> Coolify only
class FakeCoolify(http.server.BaseHTTPRequestHandler):
    calls = []
    reply = (201, None)

    def _body(self):
        n = int(self.headers.get("Content-Length") or 0)
        return self.rfile.read(n) if n else b""

    def _answer(self, code, obj):
        b = json.dumps(obj).encode()
        self.send_response(code); self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(b))); self.end_headers(); self.wfile.write(b)

    def do_PATCH(self):
        body = self._body()
        FakeCoolify.calls.append(("PATCH", self.path, body, self.headers.get("Authorization")))
        code, override = FakeCoolify.reply
        if override is not None:
            return self._answer(code, override)
        data = json.loads(body)["data"]
        # like Coolify with a token WITHOUT read:sensitive: keys come back, values don't
        self._answer(201, [{"key": d["key"], "uuid": "e-" + d["key"]} for d in data])

    def do_POST(self):
        FakeCoolify.calls.append(("POST", self.path, self._body(), self.headers.get("Authorization")))
        uuid = self.path.split("uuid=")[1].split("&")[0]
        self._answer(200, {"deployments": [{"resource_uuid": uuid, "deployment_uuid": "d-1"}]})

    def do_GET(self):
        FakeCoolify.calls.append(("GET", self.path, b"", None))
        self._answer(200, [{"key": "STRIPE_SECRET_KEY", "value": "must-never-be-asked-for"}])

    def log_message(self, *a):
        pass


def start_fake_coolify():
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), FakeCoolify)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


class CustomerTokensGoToCoolifyOnly(unittest.TestCase):
    CUSTOMER = "sk_live_customerStripe_51Hx9aQ2"

    @classmethod
    def setUpClass(cls):
        cls.srv = start_fake_coolify()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()

    def setUp(self):
        FakeCoolify.calls.clear(); FakeCoolify.reply = (201, None)
        self.saved = (coolify_handoff.COOLIFY_URL, coolify_handoff.COOLIFY_TOKEN, dict(os.environ))
        coolify_handoff.COOLIFY_URL = f"http://127.0.0.1:{self.srv.server_address[1]}"
        coolify_handoff.COOLIFY_TOKEN = "atta-coolify-token-xyz"
        os.environ["APP_BUILDER_SESSION_SECRET"] = ATTA_SECRET
        coolify_handoff.RESOURCES_FILE.write_text(json.dumps({"apps": {
            "shop": "uuid-shop", "mailer": {"uuid": "uuid-mail", "kind": "service"}}}))
        shutil.rmtree(customer_secrets.RECEIPTS, ignore_errors=True)

    def tearDown(self):
        coolify_handoff.COOLIFY_URL, coolify_handoff.COOLIFY_TOKEN, env = self.saved
        os.environ.clear(); os.environ.update(env)

    def test_delivered_to_coolify_and_only_a_receipt_kept(self):
        r = customer_secrets.deliver("shop", {"STRIPE_SECRET_KEY": self.CUSTOMER, "OPENAI_API_KEY": "sk-proj-cust-123456789"},
                                     "tester01", "b-20260925-000000-aaaaaaaa")
        self.assertEqual(r["delivered"], ["OPENAI_API_KEY", "STRIPE_SECRET_KEY"])
        method, path, body, auth = FakeCoolify.calls[0]
        self.assertEqual((method, path), ("PATCH", "/api/v1/applications/uuid-shop/envs/bulk"))
        self.assertEqual(auth, "Bearer atta-coolify-token-xyz")
        item = [d for d in json.loads(body)["data"] if d["key"] == "STRIPE_SECRET_KEY"][0]
        self.assertEqual(item["value"], self.CUSTOMER)
        self.assertTrue(item["is_literal"] and item["is_shown_once"] and item["is_runtime"])
        self.assertFalse(item["is_buildtime"])
        self.assertEqual(FakeCoolify.calls[1][0:2], ("POST", "/api/v1/deploy?uuid=uuid-shop&force=false"))
        self.assertNotIn("GET", [c[0] for c in FakeCoolify.calls])            # never reads a value back
        # The value is nowhere on ATTa's disk; the receipt has name + fingerprint + where + when.
        self.assertNotIn(self.CUSTOMER, all_text_under(ROOT))
        self.assertNotIn(self.CUSTOMER, json.dumps(r))
        rec = customer_secrets.receipts("shop")["vars"]["STRIPE_SECRET_KEY"]
        self.assertEqual(len(rec["fingerprint"]), 16)
        self.assertEqual((rec["build_id"], rec["by"], rec["delivered_to"]),
                         ("b-20260925-000000-aaaaaaaa", "tester01", "coolify:application:uuid-shop"))
        self.assertEqual(oct(customer_secrets._receipt_path("shop").stat().st_mode & 0o777), "0o600")

    def test_check_compares_without_the_old_token(self):
        customer_secrets.deliver("shop", {"STRIPE_SECRET_KEY": self.CUSTOMER}, "tester01")
        self.assertTrue(customer_secrets.check("shop", "STRIPE_SECRET_KEY", self.CUSTOMER)["matches"])
        self.assertFalse(customer_secrets.check("shop", "STRIPE_SECRET_KEY", self.CUSTOMER + "x")["matches"])
        self.assertFalse(customer_secrets.check("shop", "OTHER", self.CUSTOMER)["recorded"])
        # keyed: a bare hash of the value does not reproduce the fingerprint
        fp = customer_secrets.receipts("shop")["vars"]["STRIPE_SECRET_KEY"]["fingerprint"]
        self.assertNotIn(fp, hashlib.sha256(self.CUSTOMER.encode()).hexdigest())

    def test_service_resource(self):
        customer_secrets.deliver("mailer", {"SMTP_PASSWORD": "cust-mail-pass-99"}, "admin", redeploy=False)
        self.assertEqual(FakeCoolify.calls[0][1], "/api/v1/services/uuid-mail/envs/bulk")

    def test_nothing_kept_when_coolify_not_ready(self):
        coolify_handoff.COOLIFY_TOKEN = ""
        with self.assertRaisesRegex(customer_secrets.SecretError, "not configured"):
            customer_secrets.deliver("shop", {"STRIPE_SECRET_KEY": self.CUSTOMER}, "tester01")
        coolify_handoff.COOLIFY_TOKEN = "t"
        with self.assertRaisesRegex(customer_secrets.SecretError, "no Coolify resource"):
            customer_secrets.deliver("unmapped", {"STRIPE_SECRET_KEY": self.CUSTOMER}, "tester01")
        self.assertEqual(FakeCoolify.calls, [])
        self.assertNotIn(self.CUSTOMER, all_text_under(ROOT))
        self.assertEqual(customer_secrets.names("shop"), [])

    def test_coolify_error_echoing_the_value_is_scrubbed(self):
        FakeCoolify.reply = (422, {"message": f"invalid value {self.CUSTOMER}"})
        with self.assertRaises(customer_secrets.SecretError) as cm:
            customer_secrets.deliver("shop", {"STRIPE_SECRET_KEY": self.CUSTOMER}, "tester01")
        self.assertNotIn(self.CUSTOMER, str(cm.exception))
        self.assertEqual(customer_secrets.names("shop"), [])

    def test_refusals(self):
        bad = [{"APP_BUILDER_SESSION_SECRET": "whatever-1234"}, {"ATTA_X": "v"}, {"COOLIFY_TOKEN": "v"},
               {"SERVICE_FQDN_WEB": "v"}, {"1BAD": "v"}, {"OK_NAME": ""}, {"OK_NAME": "a\0b"},
               {"OK_NAME": "x" * 9000}, {}]
        for v in bad:
            with self.assertRaises(customer_secrets.SecretError, msg=str(v)):
                customer_secrets.deliver("shop", v, "tester01")
        # ATTa's own credential pasted as a customer token is refused, and not echoed back
        with self.assertRaises(customer_secrets.SecretError) as cm:
            customer_secrets.deliver("shop", {"ANTHROPIC_API_KEY": ATTA_SECRET}, "tester01")
        self.assertNotIn(ATTA_SECRET, str(cm.exception))
        # the customer's OWN Anthropic key under the same name is exactly what this is for
        customer_secrets.deliver("shop", {"ANTHROPIC_API_KEY": "sk-ant-customer-own-key-42"}, "tester01", redeploy=False)
        self.assertEqual(customer_secrets.names("shop"), ["ANTHROPIC_API_KEY"])
        self.assertEqual(len(FakeCoolify.calls), 1)


# ============================================================================ #2 evidence + CSRF, real gateway
def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0)); return s.getsockname()[1]


class Gateway(unittest.TestCase):
    """A real gateway.py process with its own data folder."""

    @classmethod
    def setUpClass(cls):
        cls.coolify = start_fake_coolify()
        cls.root = TMP / "gw-root"
        (cls.root / "state").mkdir(parents=True)
        u = {}
        for name, role in (("admin", "admin"), ("tester01", "user"), ("tester02", "user"), ("system", "admin")):
            u[name] = {"name": name, "role": role, "session_version": 1, "password": accounts.make_hash("pw-" + name)}
        (cls.root / "state/users.json").write_text(json.dumps({"users": u}))
        (cls.root / "front-door.html").write_text("<html><body><h1>Front Door</h1></body></html>")
        (cls.root / "state/app_owners.json").write_text(json.dumps({"appa": {"owner": "tester01", "build_id": None, "at": 0}}))
        (cls.root / "library/appa").mkdir(parents=True)
        (cls.root / "library/legacy").mkdir(parents=True)
        (cls.root / "coolify_resources.json").write_text(json.dumps({"apps": {"appa": "uuid-appa"}}))
        (cls.root / ".env").write_text(f"APP_BUILDER_SESSION_SECRET={ATTA_SECRET}\n")
        ev = cls.root / "state/runner/evidence/appa"; ev.mkdir(parents=True)
        (ev / "page.html").write_text("<html><script>fetch('/upload',{method:'POST'})</script></html>")
        (ev / "screenshot.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"\0" * 32)
        (ev / "browser.json").write_text(json.dumps({"console": ["<script>x</script>"]}))
        (ev / "notes.svg").write_text("<svg onload=alert(1)></svg>")
        (ev / "leak.txt").symlink_to(cls.root / ".env")
        fake = cls.root / "state/runner/evidence/legacy"; fake.mkdir(parents=True)
        (fake / "screenshot.png").write_text("<html><script>alert(1)</script></html>")   # not a PNG
        cls.port = free_port()
        env = {k: v for k, v in os.environ.items() if k not in ("APP_BUILDER_AUTH_DISABLED",)}
        env.update(APP_BUILDER_ROOT=str(cls.root), APP_BUILDER_PORT=str(cls.port), APP_BUILDER_HOST="127.0.0.1",
                   APP_BUILDER_SESSION_SECRET=ATTA_SECRET, COOLIFY_URL=f"http://127.0.0.1:{cls.coolify.server_address[1]}",
                   COOLIFY_TOKEN="gw-coolify-token", ATTA_ADM_ROOT=str(cls.root / "adm"))
        cls.proc = subprocess.Popen([sys.executable, str(DEP / "gateway.py")], env=env, cwd=str(DEP),
                                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        for _ in range(100):
            try:
                urllib.request.urlopen(f"http://127.0.0.1:{cls.port}/health", timeout=1); break
            except OSError:
                time.sleep(0.1)
        else:
            cls.proc.kill(); raise RuntimeError(cls.proc.stdout.read().decode())
        cls.cookies = {n: cls.login(n) for n in ("admin", "tester01", "tester02")}

    @classmethod
    def tearDownClass(cls):
        cls.proc.kill(); cls.proc.wait(); cls.coolify.shutdown()

    @classmethod
    def req(cls, path, who=None, data=None, headers=None, method=None):
        h = {"Host": f"127.0.0.1:{cls.port}", **(headers or {})}
        if who:
            h["Cookie"] = cls.cookies[who]
        r = urllib.request.Request(f"http://127.0.0.1:{cls.port}{path}", data=data, headers=h, method=method)
        class NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, *a, **k): return None
        try:
            with urllib.request.build_opener(NoRedirect).open(r, timeout=10) as resp:
                return resp.status, dict(resp.headers), resp.read()
        except urllib.error.HTTPError as e:
            return e.code, dict(e.headers), e.read()

    @classmethod
    def login(cls, name, password=None):
        code, h, _ = cls.req("/login", data=f"user={name}&password={password or 'pw-' + name}".encode(),
                             headers={"Content-Type": "application/x-www-form-urlencoded"})
        return h.get("Set-Cookie", "").split(";")[0] if code == 302 else None

    # ---- #2 evidence
    def test_page_html_is_plain_text_download(self):
        code, h, body = self.req("/evidence/appa/page.html", "tester01")
        self.assertEqual(code, 200)
        self.assertTrue(h["Content-Type"].startswith("text/plain"))
        self.assertTrue(h["Content-Disposition"].startswith("attachment"))
        self.assertEqual(h["X-Content-Type-Options"], "nosniff")
        self.assertIn("sandbox", h["Content-Security-Policy"])
        self.assertIn(b"<script>", body)   # the data itself is intact, just inert

    def test_screenshot_and_json(self):
        code, h, _ = self.req("/evidence/appa/screenshot.png", "tester01")
        self.assertEqual((code, h["Content-Type"]), (200, "image/png"))
        code, h, _ = self.req("/evidence/appa/browser.json", "tester01")
        self.assertEqual((code, h["Content-Type"]), (200, "application/json"))
        self.assertIn("sandbox", h["Content-Security-Policy"])

    def test_unknown_types_download(self):
        code, h, _ = self.req("/evidence/appa/notes.svg", "tester01")
        self.assertEqual((code, h["Content-Type"]), (200, "application/octet-stream"))
        self.assertTrue(h["Content-Disposition"].startswith("attachment"))

    def test_fake_png_is_not_rendered(self):
        code, h, _ = self.req("/evidence/legacy/screenshot.png", "admin")
        self.assertEqual(code, 200)
        self.assertNotEqual(h["Content-Type"], "image/png")
        self.assertTrue(h["Content-Disposition"].startswith("attachment"))

    def test_evidence_ownership(self):
        self.assertEqual(self.req("/evidence/appa/page.html", "tester02")[0], 404)
        self.assertEqual(self.req("/evidence/appa/page.html", "admin")[0], 200)
        self.assertEqual(self.req("/evidence/legacy/screenshot.png", "tester01")[0], 404)  # no owner: admin only
        self.assertEqual(self.req("/evidence/appa/page.html")[0], 302)                     # not logged in

    def test_evidence_cannot_escape(self):
        self.assertEqual(self.req("/evidence/appa/leak.txt", "admin")[0], 404)            # symlink to .env
        self.assertEqual(self.req("/evidence/appa/..%2F..%2F..%2F.env", "admin")[0], 404)
        self.assertEqual(self.req("/evidence/appa/.hidden", "admin")[0], 404)

    def test_pages_allow_no_script(self):
        for path in ("/builds", "/upload", "/library", "/apps/appa/secrets"):
            code, h, _ = self.req(path, "tester01")
            self.assertEqual(code, 200, path)
            csp = h["Content-Security-Policy"]
            self.assertIn("default-src 'none'", csp); self.assertNotIn("script-src", csp)
            self.assertEqual(h["X-Frame-Options"], "DENY")
        code, h, _ = self.req("/", "tester01")
        self.assertIn("frame-ancestors 'self'", h["Content-Security-Policy"])

    # ---- CSRF
    def test_cross_site_posts_refused(self):
        body = b"url=https://github.com/o/r"
        ct = {"Content-Type": "application/x-www-form-urlencoded"}
        for extra in ({"Origin": "https://evil.example"}, {"Sec-Fetch-Site": "cross-site"},
                      {"Sec-Fetch-Site": "same-site", "Origin": f"http://127.0.0.1:{self.port + 1}"},
                      {"Origin": f"http://127.0.0.1:{self.port + 1}"}, {"Origin": "null"},
                      {"Referer": "https://evil.example/page"}):
            self.assertEqual(self.req("/add-repo", "admin", body, {**ct, **extra})[0], 403, extra)
        code, h, _ = self.req("/add-repo", "admin", body, {**ct, "Origin": f"http://127.0.0.1:{self.port}",
                                                             "Sec-Fetch-Site": "same-origin"})
        self.assertEqual(code, 302)
        bid = h["Location"].rsplit("/", 1)[-1]
        rec = json.loads((self.root / "state/builds" / f"{bid}.json").read_text())
        self.assertEqual((rec["origin"], rec["owner"]), ("web", "admin"))

    def test_login_is_also_protected(self):
        code, _, _ = self.req("/login", data=b"user=admin&password=pw-admin",
                              headers={"Content-Type": "application/x-www-form-urlencoded", "Origin": "https://evil.example"})
        self.assertEqual(code, 403)

    # ---- #3 at the front door
    def test_reserved_account_cannot_log_in(self):
        self.assertIsNone(self.login("system"))
        self.assertIsNotNone(self.login("admin"))

    # ---- customer tokens through the web
    def test_customer_token_form(self):
        FakeCoolify.calls.clear(); FakeCoolify.reply = (201, None)
        token = "sk_live_fromTheForm_77aa99"
        ct = {"Content-Type": "application/x-www-form-urlencoded", "Origin": f"http://127.0.0.1:{self.port}"}
        self.assertEqual(self.req("/apps/appa/secrets", "tester02")[0], 404)
        self.assertEqual(self.req("/apps/appa/secrets", "tester02", f"name=STRIPE_KEY&value={token}".encode(), ct)[0], 404)
        code, _, body = self.req("/apps/appa/secrets", "tester01", f"name=STRIPE_KEY&value={token}&name=&value=".encode(), ct)
        self.assertEqual(code, 200, body[:400])
        self.assertIn(b"Sent to Coolify: STRIPE_KEY", body)
        self.assertNotIn(token.encode(), body)
        self.assertEqual(FakeCoolify.calls[0][1], "/api/v1/applications/uuid-appa/envs/bulk")
        self.assertNotIn(token, all_text_under(self.root))
        code, _, body = self.req("/apps/appa/secrets", "tester01")
        self.assertIn(b"STRIPE_KEY", body); self.assertNotIn(token.encode(), body)
        code, _, body = self.req("/api/apps/appa/secrets/check", "tester01",
                                 json.dumps({"name": "STRIPE_KEY", "value": token}).encode(),
                                 {"Content-Type": "application/json", "Origin": f"http://127.0.0.1:{self.port}"})
        self.assertTrue(json.loads(body)["matches"])
        code, _, body = self.req("/api/apps/appa/secrets/check", "tester01",
                                 json.dumps({"name": "STRIPE_KEY", "value": "wrong"}).encode(),
                                 {"Content-Type": "application/json"})
        self.assertFalse(json.loads(body)["matches"])
        # ATTa's own secret through the API: refused, never echoed
        code, _, body = self.req("/api/apps/appa/secrets", "admin", json.dumps({"secrets": {"X_KEY": ATTA_SECRET}}).encode(),
                                 {"Content-Type": "application/json"})
        self.assertEqual(code, 400); self.assertNotIn(ATTA_SECRET.encode(), body)


if __name__ == "__main__":
    unittest.main()
