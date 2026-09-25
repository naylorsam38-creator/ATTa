"""v117: v116's pass-1 env_file check relies on `docker compose config --no-env-resolution` leaving env files unread.
Ubuntu 24.04's Compose (2.40.3) accepts the flag and still inlines them, so that check saw nothing there (the YAML
check caught it only because PyYAML was present). compose_guard.no_env_resolution_works() probes the real behaviour.

    cd ATTa && python3 -m unittest tests.test_v117_compose_probe -v"""
import shutil, subprocess, sys, unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from atta_testlib import DEP  # noqa: E402,F401
import compose_guard  # noqa: E402


class Probe(unittest.TestCase):
    def setUp(self):
        compose_guard._NO_ENV_RESOLUTION_WORKS = None
        self.addCleanup(setattr, compose_guard, "_NO_ENV_RESOLUTION_WORKS", None)

    def probe(self, behaviour):
        def run(cmd, cwd):
            env_file = Path(cwd) / "probe.env"
            marker = env_file.read_text().split("=", 1)[1].strip()
            return behaviour(marker)
        return compose_guard.no_env_resolution_works(run)

    def test_working_flag(self):
        # env_file listed by path, its content not read
        self.assertTrue(self.probe(lambda m: (0, '{"services":{"probe":{"env_file":[{"path":"/x/probe.env"}]}}}')))

    def test_flag_accepted_but_files_still_read(self):
        # what Ubuntu 24.04's docker-compose-v2 2.40.3 prints: the value inlined, the path gone
        self.assertFalse(self.probe(lambda m: (0, '{"services":{"probe":{"environment":{"ATTA_PROBE":"%s"}}}}' % m)))

    def test_unknown_flag(self):
        self.assertFalse(self.probe(lambda m: (1, "unknown flag: --no-env-resolution")))

    def test_probed_once(self):
        calls = []
        self.probe(lambda m: calls.append(1) or (0, '{"services":{"probe":{"env_file":["probe.env"]}}}'))
        self.probe(lambda m: calls.append(1) or (1, "x"))
        self.assertEqual(len(calls), 1)

    @unittest.skipUnless(shutil.which("docker"), "docker CLI not installed")
    def test_real_compose_answers(self):
        def run(cmd, cwd):
            r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, env=compose_guard.clean_env(), timeout=60)
            return r.returncode, r.stdout + r.stderr
        self.assertIn(compose_guard.no_env_resolution_works(run), (True, False))


if __name__ == "__main__":
    unittest.main()
