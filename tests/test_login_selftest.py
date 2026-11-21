"""The Login self-test, as part of the normal suite.

    cd ATTa && python3 -m unittest discover -s tests -v       (the suite every change already runs)
    cd ATTa && python3 -m unittest tests.test_login_selftest -v

Runs 04-deployment/login_selftest.py for real: `bash run` starts a throwaway instance, Playwright signs a
new test account in and out through the browser. No mocks. It is never skipped: without Playwright, or on a
host where `bash run` would deploy the live server, it FAILS with the step that could not run, because a
login that was not checked is not a login that works. See docs/SETUP-AND-LOGIN.md, section 5.
"""
import subprocess
import sys
import unittest
from pathlib import Path

SELFTEST = Path(__file__).resolve().parent.parent / "04-deployment" / "login_selftest.py"


class LoginSelfTest(unittest.TestCase):
    def test_login_self_test_passes_end_to_end(self):
        r = subprocess.run([sys.executable, str(SELFTEST)], capture_output=True, text=True, timeout=300)
        out = r.stdout + r.stderr
        print("\n" + out, flush=True)
        failed = next((l for l in out.splitlines() if l.startswith("LOGIN SELF-TEST FAILED")), None)
        self.assertEqual(r.returncode, 0, (failed or "LOGIN SELF-TEST did not pass") + "\n" + out)
        self.assertIn("LOGIN SELF-TEST PASSED: 11/11 steps", out)


if __name__ == "__main__":
    unittest.main()
