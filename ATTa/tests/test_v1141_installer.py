"""v114.1 installer tests: .env parsing, pinned Compose, clean code releases, permissions, Docker
readiness, unit ordering. Bash functions from 04-deployment/bootstrap-lib.sh are run in throwaway
folders; no systemd needed. Permission-ownership checks need root and are skipped otherwise.

    cd ATTa && python3 -m unittest discover -s tests -v
    ATTA_TEST_NETWORK=1 ...   also downloads the pinned Compose from GitHub and verifies it for real
"""
import os, re, shutil, stat, subprocess, sys, tempfile, time, unittest
from pathlib import Path

DEP = Path(__file__).resolve().parent.parent / "04-deployment"
LIB = DEP / "bootstrap-lib.sh"
BOOTSTRAP = (DEP / "bootstrap.sh").read_text()
sys.path.insert(0, str(DEP))
import envfile  # noqa: E402


def bash(script, env=None, cwd=None):
    e = {**os.environ, "ATTA_LIB_DIR": str(DEP), **(env or {})}
    return subprocess.run(["bash", "-c", f'set -u; . "{LIB}"\n{script}'], capture_output=True, text=True,
                          env=e, cwd=cwd, timeout=120)


class Tmp(unittest.TestCase):
    def setUp(self):
        self.t = Path(tempfile.mkdtemp(prefix="atta-1141-"))


class EnvFileIsDataNotShell(Tmp):
    ATTACKS = ['X_TOKEN=$(touch {m})', 'APP_BUILDER_PASSWORD=`touch {m}`', 'APP_BUILDER_USER=${{HOME}}',
               'APP_BUILDER_PASSWORD="$(touch {m})"', 'export APP_BUILDER_USER=admin', 'touch {m}',
               'APP_BUILDER_USER=a; touch {m}"', 'bad-name=1', 'APP_BUILDER_USER=a\\b']

    def test_old_way_really_ran_code(self):
        # The problem being fixed: sourcing runs the file.
        m = self.t / "pwned-old"; f = self.t / ".env"
        f.write_text(f"X_TOKEN=$(touch {m})\n")
        subprocess.run(["bash", "-c", f'set -a; . "{f}"; set +a'], capture_output=True)
        self.assertTrue(m.exists())

    def test_attacks_rejected_and_never_run(self):
        for i, a in enumerate(self.ATTACKS):
            m = self.t / f"pwned-{i}"; f = self.t / f"env{i}"
            f.write_text("APP_BUILDER_ROOT=/srv/app-builder\n" + a.format(m=m) + "\n")
            chk = subprocess.run([sys.executable, str(DEP / "envfile.py"), "check", str(f)], capture_output=True, text=True)
            self.assertEqual(chk.returncode, 1, a)
            self.assertRegex(chk.stderr, r"line 2", a)
            r = bash(f'atta_env_run "{f}" APP_BUILDER_USER,APP_BUILDER_PASSWORD,X_TOKEN -- true')
            self.assertNotEqual(r.returncode, 0, a)
            self.assertFalse(m.exists(), a)

    def test_generated_template_passes_and_only_named_keys_are_set(self):
        f = self.t / ".env"
        f.write_text("# comment `with backticks` and $(stuff) in a comment is fine\n"
                     "APP_BUILDER_ROOT=/srv/app-builder\nAPP_BUILDER_USER=boss\nAPP_BUILDER_PASSWORD='p a s s'\n"
                     "COOLIFY_TOKEN=\nANTHROPIC_API_KEY=sk-x\nSOMETHING_ELSE=1\n\n")
        r = bash(f'env -u APP_BUILDER_USER -u ANTHROPIC_API_KEY bash -c \'. "{LIB}"; atta_env_run "{f}" '
                 f'APP_BUILDER_USER,APP_BUILDER_PASSWORD,SOMETHING_ELSE -- env\'')
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("APP_BUILDER_USER=boss", r.stdout)
        self.assertIn("APP_BUILDER_PASSWORD=p a s s", r.stdout)       # quotes removed, value literal
        self.assertNotIn("ANTHROPIC_API_KEY", r.stdout)                 # not asked for: not set
        self.assertNotIn("SOMETHING_ELSE", r.stdout)                    # unknown name: never exported
        self.assertIn("SOMETHING_ELSE is not a setting", r.stderr)      # ...but reported

    def test_errors_never_echo_values(self):
        with self.assertRaises(envfile.EnvFileError) as c:
            envfile.parse("ANTHROPIC_API_KEY=sk-secret$x\n")
        self.assertNotIn("sk-secret", str(c.exception))

    def test_bootstrap_no_longer_sources_env(self):
        self.assertIsNone(re.search(r'(^|[;(\s])(\.|source)\s+"?\$ROOT/\.env', BOOTSTRAP, re.M))


class PinnedCompose(Tmp):
    def test_arch_mapping(self):
        for m, want in (("x86_64", "x86_64"), ("amd64", "x86_64"), ("aarch64", "aarch64"), ("arm64", "aarch64")):
            r = bash("atta_arch", env={"ATTA_UNAME_M": m})
            self.assertEqual((r.returncode, r.stdout.strip()), (0, want), m)

    def test_unsupported_arch_refused_before_download(self):
        for m in ("armv7l", "i686", "riscv64", "x86_64; rm -rf /", "../../x"):
            r = bash(f'PATH=/nonexistent:$PATH; curl() {{ echo DOWNLOADED; }}; atta_install_compose "{self.t}/dc"',
                     env={"ATTA_UNAME_M": m})
            self.assertNotEqual(r.returncode, 0, m)
            self.assertNotIn("DOWNLOADED", r.stdout, m)
            self.assertIn("unsupported machine type", r.stderr, m)
            self.assertFalse((self.t / "dc").exists())

    def test_wrong_checksum_not_installed(self):
        f = self.t / "fake-compose"; f.write_bytes(b"#!/bin/sh\necho evil\n")
        r = bash(f'atta_verify_install "{f}" "$ATTA_COMPOSE_SHA256_X86_64" "{self.t}/plugins/docker-compose"')
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("CHECKSUM MISMATCH", r.stderr)
        self.assertFalse((self.t / "plugins" / "docker-compose").exists())
        self.assertFalse(f.exists())                                   # the bad download is deleted

    def test_download_that_fails_checksum_is_refused(self):
        # curl stubbed to "download" the wrong bytes: the whole install path must refuse.
        r = bash(f'curl() {{ local o; while [ $# -gt 0 ]; do [ "$1" = -o ] && o="$2"; shift; done; echo tampered > "$o"; }}\n'
                 f'atta_install_compose "{self.t}/dc"', env={"ATTA_UNAME_M": "x86_64"})
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("CHECKSUM MISMATCH", r.stderr)
        self.assertFalse((self.t / "dc").exists())

    def test_right_checksum_installs_0755(self):
        f = self.t / "c"; f.write_bytes(b"ok\n")
        import hashlib
        sha = hashlib.sha256(b"ok\n").hexdigest()
        r = bash(f'atta_verify_install "{f}" "{sha}" "{self.t}/p/docker-compose"')
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(stat.S_IMODE((self.t / "p" / "docker-compose").stat().st_mode), 0o755)

    def test_no_unpinned_download_left(self):
        self.assertNotIn("releases/latest", BOOTSTRAP)
        self.assertNotIn("releases/latest", LIB.read_text())
        self.assertIn("--proto '=https'", LIB.read_text())

    @unittest.skipUnless(os.environ.get("ATTA_TEST_NETWORK") == "1", "network test (ATTA_TEST_NETWORK=1)")
    def test_real_pinned_download_verifies(self):
        r = bash(f'atta_install_compose "{self.t}/dc" && "{self.t}/dc" version', env={"ATTA_UNAME_M": "x86_64"})
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("v5.5.1", r.stdout)


class CleanCodeReleases(Tmp):
    def _src(self, name, files, version="v1"):
        root = self.t / name
        (root / "04-deployment").mkdir(parents=True)
        (root / "release.json").write_text('{"version": "%s"}' % version)
        for rel, body in files.items():
            p = root / "04-deployment" / rel; p.parent.mkdir(parents=True, exist_ok=True); p.write_text(body)
        return root / "04-deployment"

    def deploy(self, src):
        app, rels = self.t / "app", self.t / "rels"
        r = bash(f'n="$(atta_stage_code "{src}" "{rels}")" && atta_activate_code "{app}" "{rels}" "$n" && echo "$n"')
        return r, app, rels

    def test_stale_files_disappear(self):
        r, app, rels = self.deploy(self._src("a", {"gateway.py": "x=1\n", "old_module.py": "y=2\n"}))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue((app / "old_module.py").is_file())
        r, app, rels = self.deploy(self._src("b", {"gateway.py": "x=2\n"}, "v2"))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue(app.is_symlink())
        self.assertEqual((app / "gateway.py").read_text(), "x=2\n")
        self.assertFalse((app / "old_module.py").exists())               # gone, not carried over

    def test_old_layout_migrates(self):
        app = self.t / "app"; app.mkdir(); (app / "gateway.py").write_text("legacy\n")
        r, app, rels = self.deploy(self._src("a", {"gateway.py": "new\n"}))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue(app.is_symlink())
        legacy = [p for p in rels.iterdir() if p.name.startswith("legacy-")]
        self.assertEqual(len(legacy), 1)
        self.assertEqual((legacy[0] / "gateway.py").read_text(), "legacy\n")  # kept as the previous release

    def test_broken_code_never_goes_live(self):
        r, app, rels = self.deploy(self._src("a", {"gateway.py": "ok=1\n"}))
        live = os.readlink(app)
        r, app, rels = self.deploy(self._src("b", {"gateway.py": "def broken(:\n"}, "v2"))
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("does not compile", r.stderr)
        self.assertEqual(os.readlink(app), live)                         # still the good release
        self.assertFalse(any(p.name.startswith(".incoming") for p in rels.iterdir()))
        self.assertFalse(list(rels.rglob("__pycache__")))                # the check wrote nothing

    def test_symlinks_not_copied(self):
        src = self._src("a", {"gateway.py": "x=1\n"})
        (src / "evil").symlink_to("/etc/shadow")
        r, app, rels = self.deploy(src)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertFalse((app / "evil").exists() or (app / "evil").is_symlink())

    def test_restore_and_prune(self):
        for i in range(6):
            r, app, rels = self.deploy(self._src(f"s{i}", {"gateway.py": f"v={i}\n"}, f"v{i}"))
            self.assertEqual(r.returncode, 0, r.stderr)
            time.sleep(1.05)                                             # distinct timestamps
        prev = Path((rels / ".previous").read_text().strip())
        r = bash(f'ATTA_KEEP_CODE_RELEASES=2; atta_prune_code_releases "{app}" "{rels}"')
        self.assertEqual(r.returncode, 0, r.stderr)
        left = sorted(p.name for p in rels.iterdir() if p.is_dir())
        self.assertEqual(len(left), 2, left)
        self.assertTrue(prev.is_dir())                                   # previous always kept
        self.assertEqual((app / "gateway.py").read_text(), "v=5\n")
        r = bash(f'atta_restore_previous_code "{app}" "{rels}"')
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual((app / "gateway.py").read_text(), "v=4\n")

    def test_adm_backup_copies_through_the_symlink(self):
        r, app, rels = self.deploy(self._src("a", {"gateway.py": "x=1\n"}))
        sys.path.insert(0, str(DEP / "deployd"))
        import shutil
        shutil.copytree(app, self.t / "bk", symlinks=True)
        self.assertTrue((self.t / "bk" / "gateway.py").is_file())
        self.assertFalse((self.t / "bk").is_symlink())


# v116: the layout is for services running as their OWN users (atta-web / atta-run / atta-proxy); the
# v114.1 "everything root 0600" layout would lock them out. Test users get their own names here.
T_USERS = {"ATTA_GROUP": "attat", "ATTA_WEB_USER": "attat-web", "ATTA_RUN_USER": "attat-run",
           "ATTA_PROXY_USER": "attat-proxy", "ATTA_RUN_HOME": "/tmp/attat-run-home"}


def as_user(user, *cmd):
    import pwd
    u = pwd.getpwnam(user)
    return subprocess.run(["setpriv", f"--reuid={u.pw_uid}", f"--regid={u.pw_gid}", "--init-groups", *cmd],
                          capture_output=True, text=True)


@unittest.skipUnless(os.geteuid() == 0 and shutil.which("useradd") and shutil.which("setpriv"),
                     "ownership checks need root, useradd and setpriv")
class PermissionsEveryRun(Tmp):
    def setUp(self):
        super().setUp()
        os.chmod(self.t, 0o755)
        r = bash("atta_ensure_users", env=T_USERS)
        self.assertEqual(r.returncode, 0, r.stderr)

    def layout(self):
        root = self.t / "srv"
        for d in ("state/apps", "state/builds", "state/runner/work/app1/data/db", "library/shop", "inbox"):
            (root / d).mkdir(parents=True, exist_ok=True)
        files = {".env": "SECRET=x", "TEST_ACCOUNTS.txt": "admin pw", "state/users.json": "{}",
                 "state/builds/b-1.json": "{}", "state/apps/a.json": "{}", "library/shop/.env": "STRIPE=sk_live",
                 "library/shop/index.html": "x", "state/runner/work/app1/data/db/pg": "rows",
                 "coolify_resources.json": "{}", "front-door.html": "<html>"}
        for rel, text in files.items():
            f = root / rel; f.write_text(text); f.chmod(0o600); os.chown(f, 0, 0)   # old v114.1 layout: all root
        (root / "state" / "runner" / "work" / "app1" / "data" / "db" / "pg").chmod(0o640)
        os.chown(root / "state/runner/work/app1/data/db/pg", 999, 999)              # a container's own file
        return root

    def test_each_service_gets_exactly_what_it_needs(self):
        root = self.layout()
        for _ in range(2):                                               # rerun: same result
            r = bash(f'atta_secure_state "{root}"', env=T_USERS)
            self.assertEqual(r.returncode, 0, r.stderr)
        web, run, proxy = "attat-web", "attat-run", "attat-proxy"
        ok = lambda res: self.assertEqual(res.returncode, 0, res.stderr)
        no = lambda res: self.assertNotEqual(res.returncode, 0, res.stdout)
        # secrets: root only
        for u in (web, run, proxy):
            no(as_user(u, "cat", str(root / ".env"))); no(as_user(u, "cat", str(root / "TEST_ACCOUNTS.txt")))
        # gateway: reads accounts and build records, writes the inbox and new build records
        ok(as_user(web, "cat", str(root / "state/users.json")))
        ok(as_user(web, "cat", str(root / "state/builds/b-1.json")))
        ok(as_user(web, "touch", str(root / "inbox/new.zip")))
        ok(as_user(web, "touch", str(root / "state/builds/b-2.json")))
        no(as_user(web, "touch", str(root / "library/shop/x")))           # never the library
        # runner: reads accounts, works on everything it owns, including what the gateway wrote
        ok(as_user(run, "cat", str(root / "state/users.json")))
        no(as_user(run, "touch", str(root / "state/users.json")))        # but cannot change who is admin
        ok(as_user(run, "mv", str(root / "inbox/new.zip"), str(root / "inbox/new.processed.zip")))
        ok(as_user(run, "touch", str(root / "library/shop/x")))
        ok(as_user(run, "cat", str(root / "library/shop/.env")))
        # proxy user: can pass through the data folder, nothing else
        no(as_user(proxy, "ls", str(root)))
        no(as_user(proxy, "cat", str(root / "library/shop/.env")))
        no(as_user(proxy, "ls", str(root / "state")))
        ok(as_user(proxy, "ls", str(root / "proxy")))
        # others: nothing at all
        no(as_user("nobody", "ls", str(root / "state")))
        # a container's data keeps its owner
        self.assertEqual(os.stat(root / "state/runner/work/app1/data/db/pg").st_uid, 999)
        self.assertEqual(stat.S_IMODE(os.stat(root / ".env").st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(os.stat(root).st_mode), 0o3771)
        # sticky data folder: services manage their own entries, never root's .env
        ok(as_user(run, "bash", "-c", f"rm -rf {root}/package && mkdir {root}/package"))
        for u in (web, run):
            no(as_user(u, "rm", "-f", str(root / ".env")))
            no(as_user(u, "mv", str(root / ".env"), str(root / "stolen")))
        self.assertTrue((root / ".env").is_file())


class DockerReadiness(Tmp):
    def _fake_docker(self, fail_times):
        b = self.t / "bin"; b.mkdir()
        c = self.t / "count"; c.write_text("0")
        (b / "docker").write_text(f'#!/bin/sh\nn=$(cat {c}); echo $((n+1)) > {c}\n[ "$n" -ge {fail_times} ]\n')
        (b / "docker").chmod(0o755)
        return {"PATH": f"{b}:{os.environ['PATH']}"}, c

    def test_waits_until_docker_answers(self):
        env, c = self._fake_docker(2)
        r = subprocess.run(["bash", str(DEP / "wait-for-docker.sh")], env={**os.environ, **env, "ATTA_DOCKER_WAIT": "20"},
                           capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(c.read_text().strip(), "3")                    # failed twice, then answered

    def test_gives_up_when_docker_never_answers(self):
        env, _ = self._fake_docker(10_000)
        r = subprocess.run(["bash", str(DEP / "wait-for-docker.sh")], env={**os.environ, **env, "ATTA_DOCKER_WAIT": "2"},
                           capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 1)
        self.assertIn("did not answer", r.stderr)


class UnitOrdering(unittest.TestCase):
    def unit(self, name):
        m = re.search(r"cat >/etc/systemd/system/%s <<EOF\n(.*?)\nEOF" % re.escape(name), BOOTSTRAP, re.S)
        self.assertIsNotNone(m, name)
        return m.group(1)

    def test_docker_services(self):
        for name in ("app-builder-pipeline.service", "app-builder-watcher.service"):
            u = self.unit(name)
            unit_sec = u.split("[Service]")[0]
            self.assertIn("Requires=docker.service", unit_sec, name)
            self.assertRegex(unit_sec, r"After=network-online\.target docker\.service", name)
            self.assertIn("Wants=network-online.target", unit_sec, name)
            self.assertIn("StartLimitIntervalSec=", unit_sec, name)       # [Unit] is where systemd reads it
            self.assertIn("StartLimitBurst=", unit_sec, name)
            self.assertIn("ExecStartPre=/usr/bin/env bash $APP/wait-for-docker.sh", u, name)

    def test_gateway_does_not_need_docker(self):
        u = self.unit("app-builder-gateway.service")
        self.assertNotIn("docker.service", u)
        self.assertIn("StartLimitBurst=", u.split("[Service]")[0])

    def test_metadata_block_and_daemon_json_kept(self):
        egress = (DEP / "container-egress.sh").read_text()        # v116: the rules moved into their own script
        self.assertIn("169.254.169.254/32", egress)
        self.assertIn("fd00:ec2::254/128", egress)
        self.assertIn('"$APP/container-egress.sh" /usr/local/sbin/atta-block-metadata', BOOTSTRAP)
        self.assertIn('if [ ! -f /etc/docker/daemon.json ]', BOOTSTRAP)  # never overwrites an existing one

    def test_bootstrap_parses(self):
        for f in ("bootstrap.sh", "bootstrap-lib.sh", "wait-for-docker.sh"):
            r = subprocess.run(["bash", "-n", str(DEP / f)], capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, f + r.stderr)


if __name__ == "__main__":
    unittest.main()
