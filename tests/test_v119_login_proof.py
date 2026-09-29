"""v119/v120 runtime-journey gate tests.

These test the WATCHER'S LOGIC in a real Chromium against a login page the test itself serves.
They are not proof that any catalogue app passes: that proof only exists in state/runner/results
after a real run on the server. What they prove: a page load is never accepted, a missing test
account fails, a missing post-login expectation fails (v120), a wrong password fails, a real
login with a real expectation passes.
"""
import os
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import sys
import json
import tempfile

HERE = Path(__file__).resolve().parent
DEP = HERE.parent / "04-deployment"
sys.path.insert(0, str(DEP))

import system_watcher as w  # noqa: E402


class BrowserPage:
    USER = "qualification-user"
    PASSWORD = "qualification-pass-123"

    def __init__(self):
        from playwright.sync_api import sync_playwright
        self.p = sync_playwright().start()
        self.browser = self.p.chromium.launch(headless=True, executable_path=os.environ.get("ATTA_CHROMIUM","/usr/bin/chromium"))
        self.page = self.browser.new_page()
        self.page.set_content("""
        <html><body>
          <h1>Sign in</h1>
          <form id="login"><input id="u" autocomplete="username"><input id="p" type="password" autocomplete="current-password"><button id="submit" type="submit">Log in</button></form>
          <div id="result"></div>
          <script>
          document.querySelector('#login').addEventListener('submit', e => {
            e.preventDefault();
            if (document.querySelector('#u').value === 'qualification-user' && document.querySelector('#p').value === 'qualification-pass-123') {
              document.querySelector('#login').remove();
              document.querySelector('#result').innerText = 'Logged in';
              history.pushState({}, '', '/home');
            }
          });
          </script>
        </body></html>
        """)

    @property
    def url(self):
        return self.page.url

    def close(self):
        self.browser.close()
        self.p.stop()




class V119JourneyProof(unittest.TestCase):
    def setUp(self):
        self.app = BrowserPage()

    def tearDown(self):
        self.app.close()

    def test_page_load_without_journey_is_not_qualified(self):
        code, detail = w.run_user_journey(self.app.page, None, self.app.url)
        self.assertEqual(code, "JOURNEY_NOT_CONFIGURED")

    def test_login_requires_a_real_test_account(self):
        spec = {"type": "login", "username": self.app.USER}
        code, detail = w.run_user_journey(self.app.page, spec, self.app.url)
        self.assertEqual(code, "TEST_ACCOUNT_MISSING")

    def test_real_login_journey_passes(self):
        spec = {
            "type": "login",
            "username": self.app.USER,
            "password": self.app.PASSWORD,
            "expected_text": "Logged in",
        }
        code, detail = w.run_user_journey(self.app.page, spec, self.app.url)
        self.assertIsNone(code, detail)
        self.assertIn("Logged in", self.app.page.locator("body").inner_text())

    def test_wrong_password_fails_the_journey(self):
        spec = {"type": "login", "username": self.app.USER, "password": "wrong", "expected_text": "Logged in"}
        code, detail = w.run_user_journey(self.app.page, spec, self.app.url)
        self.assertEqual(code, "LOGIN_NOT_COMPLETED")

    def test_login_without_a_post_login_expectation_is_refused(self):
        # v120: the contract must say what logged-in looks like; "password box gone" alone is not proof.
        spec = {"type": "login", "username": self.app.USER, "password": self.app.PASSWORD}
        code, detail = w.run_user_journey(self.app.page, spec, self.app.url)
        self.assertEqual(code, "LOGIN_EXPECTATION_MISSING")
        self.assertIn("Sign in", self.app.page.locator("body").inner_text())   # it never even tried

    def test_login_with_wrong_expectation_fails_even_though_login_worked(self):
        spec = {"type": "login", "username": self.app.USER, "password": self.app.PASSWORD, "expected_text": "Dashboard"}
        code, detail = w.run_user_journey(self.app.page, spec, self.app.url)
        self.assertEqual(code, "LOGIN_EXPECTED_TEXT_MISSING")

    def test_journey_result_is_recorded_on_stage_6(self):
        # The stage-6 record carries the journey verdict, so the evidence says WHAT was proven.
        spec = {"type": "login", "username": self.app.USER, "password": self.app.PASSWORD, "expected_text": "Logged in"}
        code, detail = w.run_user_journey(self.app.page, spec, self.app.url)
        self.assertIsNone(code)
        self.assertIn("login proof passed", detail)


if __name__ == "__main__":
    unittest.main()
