#!/usr/bin/env python3
"""login_selftest.py — the Login self-test. Run it after ANY change, before calling the work complete.

    python3 04-deployment/login_selftest.py            # exit 0 = every step passed, 1 = a step failed
    python3 04-deployment/login_selftest.py --keep     # keep the throwaway instance folder afterwards

It also runs as part of the normal suite: `python3 -m unittest discover -s tests -v`
(tests/test_login_selftest.py). Full description: docs/SETUP-AND-LOGIN.md, section 5.

What it does, for real, with no mocks:
  0  preflight     refuses to run anywhere `bash run` would touch a live system (see below)
  1  launcher      `bash run` (the real launcher) starts a throwaway instance in its own folder, on a free port
  2  health        GET /health answers OK, and the process on that port is THIS checkout's gateway.py
  3  account       a new test account is created the only way ATTa creates accounts: `accounts.py add`
  4  desk          a real Chromium (Playwright) opens /, is sent to the /login sheet, and finds the form
  5  wrong-pw      a wrong password is refused (401 "Login failed") and no session cookie is set
  6  sign-in       the right password signs in: back on the Front Door, HttpOnly session cookie, name in the nav
  7  protected     /builds (a page that needs a session) loads, /api/me names the test account
  8  sign-out      the "Log out" link ends the browser session; /builds now sends the browser back to /login
  9  sign-in-again the same account signs in again and /builds loads again
 10  stop          `bash run stop` stops the instance and the port stops answering

On failure it prints   LOGIN SELF-TEST FAILED at step N (<name>): <what was expected> / <what happened>
plus a screenshot path and the last lines of gateway.log, and exits 1.

Safety. It never touches a live system:
  - Its instance lives in a new temporary folder (APP_BUILDER_ROOT) with its own .env, secret and accounts.
  - It picks a port nothing answers on, so `bash run` can never say "Already running" about another instance.
  - It refuses to run on a systemd Linux host: there `bash run` is the full server deploy (bootstrap.sh into
    /srv/app-builder and /opt/app-builder), which is the live system.
  - It strips ATTa's own settings (APP_BUILDER_*, COOLIFY_*, ANTHROPIC_API_KEY, ...) from the environment it
    hands the launcher, so nothing from the caller's shell (a live root, a token, AUTH_DISABLED) leaks in.
"""
from __future__ import annotations

import argparse
import json
import os
import secrets
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import traceback
import urllib.error
import urllib.request
from pathlib import Path

# ===================== CONFIG — edit here, nothing below needs reading =====================
HERE = Path(__file__).resolve().parent            # .../04-deployment
REPO = HERE.parent                                # the checkout under test
LAUNCHER = REPO / "run"                            # the real launcher
ACCOUNTS = HERE / "accounts.py"
GATEWAY = HERE / "gateway.py"
# Seconds to wait for the launcher, a page, and the stop.
LAUNCH_TIMEOUT = 90
PAGE_TIMEOUT_MS = 20_000
STOP_TIMEOUT = 20
# Environment variables never passed to the launcher (they could point it at a live system or skip the login).
STRIP_PREFIXES = ("APP_BUILDER_", "ATTA_", "COOLIFY_", "DOCKERHUB_")
STRIP_NAMES = ("ANTHROPIC_API_KEY", "ALERT_WEBHOOK_URL")
# Optional Chromium binary (same variable the v119 tests use). Unset = Playwright's own browser.
CHROMIUM_ENV = "ATTA_CHROMIUM"
FALLBACK_CHROMIUMS = ("/opt/pw-browsers/chromium",)
# ==========================================================================================

STEPS = ["preflight", "launcher", "health", "account", "desk", "wrong-pw", "sign-in",
         "protected", "sign-out", "sign-in-again", "stop"]


class StepFailed(Exception):
    def __init__(self, step: str, detail: str):
        super().__init__(detail)
        self.step, self.detail = step, detail


def say(*a):
    print(*a, flush=True)


def free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def http_get(url: str, timeout: float = 3.0) -> tuple[int, bytes]:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return r.status, r.read(200)
    except urllib.error.HTTPError as e:
        return e.code, b""


def answers(port: int) -> bool:
    try:
        http_get(f"http://127.0.0.1:{port}/health", 1.0)
        return True
    except Exception:
        return False


def clean_env(root: Path) -> dict:
    env = {k: v for k, v in os.environ.items()
           if not k.startswith(STRIP_PREFIXES) and k not in STRIP_NAMES}
    env["APP_BUILDER_ROOT"] = str(root)
    return env


class SelfTest:
    def __init__(self, keep: bool = False):
        self.keep = keep
        self.root: Path | None = None
        self.port: int | None = None
        self.env: dict = {}
        self.user = "selftest-" + secrets.token_hex(4)
        self.password = ""
        self.passed: list[str] = []
        self.shots: list[str] = []
        self.page = None
        self._pw = self._browser = None

    # ---- helpers --------------------------------------------------------------------------
    def url(self, path: str) -> str:
        return f"http://127.0.0.1:{self.port}{path}"

    def ok(self, step: str, detail: str):
        self.passed.append(step)
        say(f"PASS  step {STEPS.index(step):>2} {step:<14} {detail}")

    def shot(self, step: str):
        if not self.page or not self.root:
            return
        try:
            p = self.root / f"selftest-{STEPS.index(step):02d}-{step}.png"
            self.page.screenshot(path=str(p), full_page=True)
            self.shots.append(str(p))
        except Exception:
            pass

    def session_cookie(self):
        for c in self.page.context.cookies(self.url("/")):
            if c["name"] == "session" and c["value"]:
                return c
        return None

    def path(self) -> str:
        from urllib.parse import urlparse
        return urlparse(self.page.url).path

    # ---- steps ----------------------------------------------------------------------------
    def preflight(self):
        st = "preflight"
        if sys.platform.startswith("linux") and Path("/run/systemd/system").is_dir():
            raise StepFailed(st, "this is a systemd Linux host: here `bash run` is the FULL SERVER DEPLOY "
                                 "(bootstrap.sh into /srv/app-builder and /opt/app-builder), i.e. the live system. "
                                 "Refusing. Run the self-test in a dev container/laptop copy of the checkout.")
        for f in (LAUNCHER, ACCOUNTS, GATEWAY, REPO / "02-front-door" / "front-door.html"):
            if not f.is_file():
                raise StepFailed(st, f"missing file the login needs: {f}")
        if not shutil.which("curl"):
            raise StepFailed(st, "curl is not installed; the launcher uses it for its health wait")
        try:
            import playwright.sync_api  # noqa: F401
        except ImportError:
            raise StepFailed(st, "Playwright for Python is not installed (python3 -m pip install playwright); "
                                 "the self-test needs a real browser and will not pass without one")
        self.ok(st, f"checkout {REPO}")

    def launch(self):
        st = "launcher"
        self.root = Path(tempfile.mkdtemp(prefix="atta-login-selftest-"))
        self.port = free_port()
        if answers(self.port):
            raise StepFailed(st, f"port {self.port} already answers; refusing to test someone else's instance")
        # The same .env the launcher writes itself (run: ensure_env), with our own free port. The launcher keeps
        # an .env that has APP_BUILDER_SESSION_SECRET, so it uses this one.
        env_file = self.root / ".env"
        env_file.write_text("\n".join([
            f"APP_BUILDER_ROOT={self.root}", "APP_BUILDER_HOST=127.0.0.1", f"APP_BUILDER_PORT={self.port}",
            f"APP_BUILDER_SESSION_SECRET={secrets.token_urlsafe(48)}", "APP_BUILDER_BROWSER_CHECK=true",
            "APP_BUILDER_WATCHER_INTERVAL=300", "COOLIFY_URL=", "COOLIFY_TOKEN=", ""]))
        os.chmod(env_file, 0o600)
        self.env = clean_env(self.root)
        # cwd = the throwaway folder: the launcher's tail (first_boot) touches .initialized in the cwd.
        try:
            r = subprocess.run(["bash", str(LAUNCHER)], cwd=self.root, env=self.env, capture_output=True,
                               text=True, timeout=LAUNCH_TIMEOUT)
        except subprocess.TimeoutExpired:
            raise StepFailed(st, f"`bash run` did not finish within {LAUNCH_TIMEOUT}s")
        out = (r.stdout + r.stderr).strip()
        if r.returncode != 0:
            raise StepFailed(st, f"`bash run` exited {r.returncode}:\n{out[-2000:]}")
        if "Already running." in out:
            raise StepFailed(st, "`bash run` said 'Already running.': it found another instance on our port")
        want = f"http://127.0.0.1:{self.port}/"
        if "RUNNING (local instance)" not in out or want not in out:
            raise StepFailed(st, f"`bash run` did not report 'RUNNING (local instance)' at {want}:\n{out[-2000:]}")
        self.ok(st, f"`bash run` -> RUNNING at {want} (root {self.root})")

    def health(self):
        st = "health"
        try:
            code, body = http_get(self.url("/health"))
        except Exception as e:
            raise StepFailed(st, f"GET /health failed: {e}")
        if code != 200 or body.strip() != b"OK":
            raise StepFailed(st, f"GET /health -> {code} {body!r}, expected 200 'OK'")
        pidf = self.root / "gateway.pid"
        try:
            pid = int(pidf.read_text().strip())
            cmd = Path(f"/proc/{pid}/cmdline").read_bytes().split(b"\0") if Path("/proc").is_dir() else None
        except (OSError, ValueError) as e:
            raise StepFailed(st, f"cannot read the gateway pid from {pidf}: {e}")
        if cmd is not None and str(GATEWAY).encode() not in cmd:
            raise StepFailed(st, f"pid {pid} is not this checkout's gateway.py: {b' '.join(cmd)!r}")
        self.ok(st, f"/health 200 OK, pid {pid} runs {GATEWAY}")

    def account(self):
        st = "account"
        r = subprocess.run([sys.executable, str(ACCOUNTS), "add", self.user], env=self.env, cwd=self.root,
                           capture_output=True, text=True, timeout=60)
        if r.returncode != 0 or not r.stdout.strip():
            raise StepFailed(st, f"`accounts.py add {self.user}` exited {r.returncode}: {r.stderr.strip()}")
        self.password = r.stdout.strip().splitlines()[-1]
        users = json.loads((self.root / "state" / "users.json").read_text())["users"]
        u = users.get(self.user) or {}
        if u.get("role") != "user" or u.get("disabled") or "hash" not in (u.get("password") or {}):
            raise StepFailed(st, f"state/users.json has no usable entry for {self.user}: {sorted(u)}")
        self.ok(st, f"created {self.user} (role user) in {self.root}/state/users.json")

    def open_browser(self):
        from playwright.sync_api import sync_playwright
        self._pw = sync_playwright().start()
        exe = os.environ.get(CHROMIUM_ENV)
        kw = {"headless": True}
        if exe and Path(exe).exists():
            kw["executable_path"] = exe
        try:
            self._browser = self._pw.chromium.launch(**kw)
        except Exception as first:
            fb = next((p for p in FALLBACK_CHROMIUMS if Path(p).exists()), None)
            if not fb:
                raise StepFailed("desk", f"Chromium would not start: {first}")
            self._browser = self._pw.chromium.launch(headless=True, executable_path=fb)
        self.page = self._browser.new_context().new_page()
        self.page.set_default_timeout(PAGE_TIMEOUT_MS)

    def desk(self):
        st = "desk"
        self.open_browser()
        resp = self.page.goto(self.url("/"))
        if self.path() != "/login":
            raise StepFailed(st, f"GET / without a session should end on /login, ended on {self.page.url} "
                                 f"(status {resp.status if resp else '?'}). Is APP_BUILDER_AUTH_DISABLED set?")
        for sel in ("form[action='/login'] input[name=user]", "form[action='/login'] input[name=password][type=password]",
                    "form[action='/login'] button"):
            if self.page.locator(sel).count() != 1:
                raise StepFailed(st, f"login sheet is missing {sel}")
        self.ok(st, "GET / -> /login sheet with user, password and 'Log in'")

    def submit(self, user: str, password: str):
        self.page.goto(self.url("/login"))
        self.page.fill("input[name=user]", user)
        self.page.fill("input[name=password]", password)
        with self.page.expect_navigation():
            self.page.click("form[action='/login'] button")

    def wrong_password(self):
        st = "wrong-pw"
        self.submit(self.user, self.password + "x")
        body = self.page.locator("body").inner_text()
        if "Login failed" not in body:
            raise StepFailed(st, f"a wrong password was not refused; page says: {body[:300]!r}")
        if self.session_cookie():
            raise StepFailed(st, "a wrong password still set a session cookie")
        self.ok(st, "wrong password -> 'Login failed', no session cookie")

    def sign_in(self, st: str):
        self.submit(self.user, self.password)
        if self.path() != "/":
            body = self.page.locator("body").inner_text()[:300]
            raise StepFailed(st, f"after signing in expected the Front Door at /, got {self.page.url}: {body!r}")
        c = self.session_cookie()
        if not c:
            raise StepFailed(st, "signed in but the browser holds no session cookie (Secure cookie over http? "
                                 "X-Forwarded-Proto?)")
        if not c.get("httpOnly"):
            raise StepFailed(st, "session cookie is not HttpOnly")
        nav = self.page.locator("#ab-nav")
        if nav.count() != 1 or self.user not in nav.inner_text() or nav.locator("a[href='/logout']").count() != 1:
            raise StepFailed(st, "Front Door has no #ab-nav naming the account with a 'Log out' link "
                                 "(front-door.html replaced, or front_door() changed?)")
        self.ok(st, f"signed in as {self.user}: Front Door at /, HttpOnly session cookie, name + 'Log out' in nav")

    def protected(self, st: str = "protected"):
        resp = self.page.goto(self.url("/builds"))
        if self.path() != "/builds" or not resp or resp.status != 200:
            raise StepFailed(st, f"/builds with a session should load (200), got {resp.status if resp else '?'} "
                                 f"at {self.page.url}")
        if self.page.locator("h1").first.inner_text().strip() != "My builds":
            raise StepFailed(st, f"/builds heading is {self.page.locator('h1').first.inner_text()!r}, expected 'My builds'")
        # ATTa's pages carry CSP default-src 'none', so in-page fetch() is blocked by design; the context's
        # request API sends the browser's own cookies instead.
        r = self.page.context.request.get(self.url("/api/me"), max_redirects=0)
        if r.status != 200:
            raise StepFailed(st, f"/api/me with the browser's session answered {r.status}, expected 200")
        me = r.json()
        if me != {"name": self.user, "role": "user"}:
            raise StepFailed(st, f"/api/me says {me!r}, expected {{'name': {self.user!r}, 'role': 'user'}}")
        self.ok(st, f"/builds 200 'My builds'; /api/me = {json.dumps(me)}")

    def sign_out(self):
        st = "sign-out"
        link = self.page.locator("nav a[href='/logout']")
        if link.count() != 1:
            raise StepFailed(st, "no 'Log out' link in the page nav on /builds")
        with self.page.expect_navigation():
            link.click()
        if self.path() != "/login":
            raise StepFailed(st, f"'Log out' should land on /login, landed on {self.page.url}")
        if self.session_cookie():
            raise StepFailed(st, "after 'Log out' the browser still holds a session cookie")
        self.page.goto(self.url("/builds"))
        if self.path() != "/login":
            raise StepFailed(st, f"after 'Log out', /builds should send the browser to /login, got {self.page.url}")
        self.ok(st, "'Log out' -> /login, cookie cleared, /builds now redirects to /login")

    def sign_in_again(self):
        st = "sign-in-again"
        self.sign_in(st)
        self.passed.pop()  # sign_in recorded under this step's name; re-record with the protected check
        self.protected(st)

    def stop(self):
        st = "stop"
        self.close_browser()
        r = subprocess.run(["bash", str(LAUNCHER), "stop"], cwd=self.root, env=self.env, capture_output=True,
                           text=True, timeout=STOP_TIMEOUT + 10)
        out = (r.stdout + r.stderr).strip()
        if r.returncode != 0 or "Stopped." not in out:
            raise StepFailed(st, f"`bash run stop` exited {r.returncode}: {out[-500:]}")
        if answers(self.port):
            raise StepFailed(st, f"port {self.port} still answers after `bash run stop`")
        self.ok(st, f"`bash run stop` -> Stopped., port {self.port} closed")

    # ---- plumbing -------------------------------------------------------------------------
    def close_browser(self):
        for x in (self._browser,):
            try:
                x and x.close()
            except Exception:
                pass
        try:
            self._pw and self._pw.stop()
        except Exception:
            pass
        self._browser = self._pw = None

    def emergency_stop(self):
        """After a failure: stop only OUR instance (its pid files live in our own root)."""
        self.close_browser()
        if self.root and (self.root / "gateway.pid").exists():
            subprocess.run(["bash", str(LAUNCHER), "stop"], cwd=self.root, env=self.env,
                           capture_output=True, timeout=STOP_TIMEOUT + 10)

    def run(self) -> int:
        say("LOGIN SELF-TEST  " + time.strftime("%Y-%m-%d %H:%M:%S") + f"  checkout {REPO}")
        step = "preflight"
        try:
            plan = [("preflight", self.preflight), ("launcher", self.launch), ("health", self.health),
                    ("account", self.account), ("desk", self.desk), ("wrong-pw", self.wrong_password),
                    ("sign-in", lambda: self.sign_in("sign-in")), ("protected", self.protected),
                    ("sign-out", self.sign_out), ("sign-in-again", self.sign_in_again), ("stop", self.stop)]
            for step, fn in plan:
                fn()
        except Exception as e:
            if isinstance(e, StepFailed):
                step, detail = e.step, e.detail
            else:
                detail = f"{type(e).__name__}: {e}\n{traceback.format_exc(limit=4)}"
            self.shot(step)
            n = STEPS.index(step) if step in STEPS else "?"
            say("")
            say("=" * 78)
            say(f"LOGIN SELF-TEST FAILED at step {n} ({step})")
            say("=" * 78)
            say(detail)
            if self.shots:
                say("screenshot: " + self.shots[-1])
            log = (self.root / "gateway.log") if self.root else None
            if log and log.exists() and log.read_text().strip():
                say("--- last lines of gateway.log ---")
                say("\n".join(log.read_text().splitlines()[-20:]))
            say(f"passed before the failure: {', '.join(self.passed) or 'none'}")
            self.emergency_stop()
            if self.root:
                say(f"instance folder kept for inspection: {self.root}")
            say("=" * 78)
            return 1
        say(f"LOGIN SELF-TEST PASSED: {len(self.passed)}/{len(STEPS)} steps")
        if self.keep:
            say(f"instance folder kept: {self.root}")
        elif self.root:
            shutil.rmtree(self.root, ignore_errors=True)
        return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="ATTa Login self-test (real launcher, real browser)")
    ap.add_argument("--keep", action="store_true", help="keep the throwaway instance folder after a pass")
    a = ap.parse_args(argv)
    return SelfTest(keep=a.keep).run()


if __name__ == "__main__":
    raise SystemExit(main())
