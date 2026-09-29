#!/usr/bin/env python3
"""
journey_author.py — v121: the half that FEEDS the gate.

The watcher (system_watcher.py, stage 6) refuses to qualify a web app unless a real user journey
is performed and proven. This module gives every running app a journey to be judged on:

  1. PROVISION a test account in the app, by CLASS of app (never by app name):
       stored     an account this module created on an earlier run (state/accounts/<id>.json)
       supplied   a contract a person or the self-healer put on the shelf (recipes/<id>.journey.json)
       env_admin  the app takes its first admin from environment variables the runner already set
                  (any USER/PASSWORD pair in the recipe env, the app's .env, or the rendered compose)
       exec       the recipe carries an `account` command to run inside the container (app CLI)
       signup     the app offers a sign-up / register / first-run-setup form: fill it in a real browser
  2. AUTHOR the journey contract from what was actually observed:
       login  → username/password as `store:<id>` references (secrets never enter targets, recipes
                or evidence) plus a post-login expectation DERIVED from the real post-login page
       smoke  → for an app with no login: click a real control and expect the real result
  3. DRY-RUN the authored contract once with the watcher's own journey code. A contract that does not
     pass here is not handed on: the failure code is recorded instead, so the fleet report says why.

The watcher still owns the verdict. This module never writes a result, never marks an app passed;
it only produces the contract the watcher then executes itself, on the proxied URL, in its own browser.

Every outcome carries a CODE. The fleet report (journey_report) groups the whole catalogue by code so
failures are fixed by class, not per app.

Command line (on the server, for one app that is running):
    python3 journey_author.py <app> <url>          author and print the contract + code
    python3 journey_author.py --report             write state/checklists/journeys.md from results
"""
from __future__ import annotations
import json, os, re, secrets, shutil, subprocess, sys, time
from pathlib import Path
from urllib.parse import urlparse

# ===================== CONFIG — edit here, nothing below needs reading =====================
# Where ATTa keeps its state on the box. Same variable every other ATTa script reads.
ROOT = Path(os.environ.get("APP_BUILDER_ROOT", "/srv/app-builder"))
# Where created test accounts are kept (one file per app, mode 600). The journey contract only
# ever says `store:<app id>`; the watcher reads the real values from here at check time.
ACCOUNTS = ROOT / "state" / "accounts"
# Where supplied journey contracts live (a person or the self-healer puts <app id>.journey.json here).
RECIPES = ROOT / "state" / "runner" / "recipes"
# Where the runner keeps each app's rendered compose file (read to find admin env vars).
WORK = ROOT / "state" / "runner" / "work"
# The library (read to find the app's .env).
LIB = ROOT / "library"
# Where qualification results are (read by --report).
RESULTS = ROOT / "state" / "runner" / "results"
# The identity used when this module CREATES an account. Change it and every new account changes;
# accounts already stored keep the values they were created with.
ACCOUNT_USER = os.environ.get("APP_BUILDER_QA_USER", "atta-qa")
ACCOUNT_EMAIL = os.environ.get("APP_BUILDER_QA_EMAIL", "atta-qa@example.com")
ACCOUNT_NAME = os.environ.get("APP_BUILDER_QA_NAME", "ATTa QA")
# Generated password length (hex chars). Longer never hurts; some apps reject < 8.
PASSWORD_HEX = 12
# How long the browser waits for a page or an action, in ms. Raise on a slow box.
NAV_TIMEOUT_MS = int(os.environ.get("APP_BUILDER_JOURNEY_TIMEOUT_MS", "20000"))
# How long to wait for the page to settle after a submit, in ms (also written into the contract).
SETTLE_MS = int(os.environ.get("APP_BUILDER_JOURNEY_SETTLE_MS", "6000"))
# After a setup wizard submits, how long to wait for the app to answer again (it may restart), seconds.
RESTART_WAIT_S = int(os.environ.get("APP_BUILDER_JOURNEY_RESTART_WAIT", "60"))
# Words that identify a sign-up / register / first-run link or button (case-insensitive regex).
SIGNUP_WORDS = r"sign ?up|register|create (an )?account|get started|create admin|set ?up|first user|initial setup|welcome"
# Words that identify a "log in" link on a landing page (to reach the login form).
LOGIN_WORDS = r"log ?in|sign ?in"
# Words on the button that submits a sign-up / setup form (regex, case-insensitive).
SUBMIT_WORDS = r"install|create|sign ?up|register|continue|submit|finish|get started|next|save|done|complete"
# Env var names that carry a first/admin USER and PASSWORD (case-insensitive regex on the name).
ADMIN_USER_VARS = r"(ADMIN|ROOT|INITIAL|DEFAULT|FIRST|SUPER|SETUP)[A-Z_]*(USER|USERNAME|LOGIN|EMAIL|NAME|ACCOUNT)$|^(ADMIN|USER|USERNAME|EMAIL)$"
ADMIN_PASS_VARS = r"(ADMIN|ROOT|INITIAL|DEFAULT|FIRST|SUPER|SETUP)[A-Z_]*(PASS|PASSWORD|PW|PASSWD)$|^(PASSWORD|ADMIN_PASSWORD)$"
# Post-login markers, tried in order. The first one PRESENT after login and ABSENT before it becomes
# the contract's expected_selector. Add a line for a marker family you see on the box.
POST_LOGIN_MARKERS = [
    'a[href*="logout" i]', 'a[href*="sign-out" i]', 'a[href*="signout" i]', 'form[action*="logout" i]',
    'button:has-text("Log out")', 'button:has-text("Logout")', 'button:has-text("Sign out")',
    'a[href*="/settings" i]', 'a[href*="/account" i]', 'a[href*="/profile" i]', 'a[href*="/dashboard" i]',
    '[aria-label*="account" i]', '[aria-label*="user menu" i]', '[data-testid*="user" i]', 'nav',
]
# On a setup wizard, a <select> option matching this (regex) is chosen: the app's embedded database.
EMBEDDED_DB_WORDS = r"sqlite|embedded|built-?in"
# Internal links never clicked for a smoke journey (regex on href).
SMOKE_SKIP = r"^(#|mailto:|tel:|javascript:)|logout|sign-?out|delete|remove|destroy|\.(pdf|zip|tar|gz|exe|dmg)$"
# How many candidate controls a smoke journey tries before giving up.
SMOKE_TRIES = 6
# ==========================================================================================

def _log(msg): print(msg, flush=True)

def safe_id(name: str) -> str:
    return re.sub(r"[^a-z0-9-]+", "-", str(name).lower()).strip("-") or "app"


# ------------------------------------------------------------------ browser
def chromium_executable():
    return (os.environ.get("APP_BUILDER_CHROMIUM_PATH")
            or next((x for x in (shutil.which("chromium"), shutil.which("chromium-browser"), shutil.which("google-chrome")) if x), None))


class Browser:
    def __init__(self):
        from playwright.sync_api import sync_playwright
        self.p = sync_playwright().start()
        exe = chromium_executable()
        self.b = self.p.chromium.launch(headless=True, executable_path=exe) if exe else self.p.chromium.launch(headless=True)
        self.ctx = self.b.new_context(ignore_https_errors=True)
        self.ctx.set_default_timeout(NAV_TIMEOUT_MS)

    def page(self):
        return self.ctx.new_page()

    def fresh(self):
        """A new context: no cookies, no session. The dry-run must log in from nothing."""
        self.ctx.close()
        self.ctx = self.b.new_context(ignore_https_errors=True)
        self.ctx.set_default_timeout(NAV_TIMEOUT_MS)
        return self.ctx.new_page()

    def close(self):
        try: self.b.close()
        except Exception: pass
        try: self.p.stop()
        except Exception: pass


def _settle(page, ms=None):
    """Network idle, then the page's TEXT stops changing (single-page apps render after the network
    goes quiet). Bounded by ms."""
    ms = ms or SETTLE_MS
    t0 = time.time()
    try: page.wait_for_load_state("networkidle", timeout=ms)
    except Exception: pass
    last = None
    while (time.time() - t0) * 1000 < ms:
        try: page.wait_for_timeout(300)
        except Exception: return
        cur = _text(page)
        if cur == last: return
        last = cur


def _wait_up(page, url: str, seconds: int) -> bool:
    """True once the app answers TWICE, 3 s apart (setup wizards restart the server, sometimes a
    moment after they have already answered the submit)."""
    t0 = time.time(); ok = 0
    while time.time() - t0 < seconds:
        try:
            page.goto(url); _settle(page, 3000); ok += 1
            if ok >= 2: return True
            time.sleep(3)
        except Exception:
            ok = 0; time.sleep(2)
    return False


def _has_login(page) -> bool:
    return bool(page.locator('input[type="password"]').count())


def _text(page) -> str:
    try: return page.locator("body").inner_text(timeout=3000)
    except Exception: return ""


# ------------------------------------------------------------------ account store
def _store_path(aid: str) -> Path:
    return ACCOUNTS / f"{aid}.json"

def load_account(aid: str) -> dict | None:
    try: return json.loads(_store_path(aid).read_text())
    except (OSError, ValueError): return None

def save_account(aid: str, username: str, password: str, how: str) -> None:
    ACCOUNTS.mkdir(parents=True, exist_ok=True)
    p = _store_path(aid)
    p.write_text(json.dumps({"username": username, "password": password, "created_by": how, "created_at": time.time()}, indent=2) + "\n")
    os.chmod(p, 0o600)

def forget_account(aid: str) -> None:
    _store_path(aid).unlink(missing_ok=True)


# ------------------------------------------------------------------ credential sources by class
def _env_pairs(app: str, part: dict) -> list[tuple[str, str, str]]:
    """(username, password, where) pairs from env the app was started with. Class env_admin."""
    envs: list[tuple[str, dict]] = []
    if isinstance(part.get("env"), dict):
        envs.append(("recipe env", {str(k): str(v) for k, v in part["env"].items()}))
    d = LIB / app
    for envf in (d / ".env",):
        if envf.is_file():
            kv = {}
            for line in envf.read_text(errors="replace").splitlines():
                if "=" in line and not line.lstrip().startswith("#"):
                    k, v = line.split("=", 1); kv[k.strip()] = v.strip().strip("\"'")
            envs.append((".env", kv))
    try:
        rendered = json.loads((WORK / safe_id(app) / "compose.rendered.json").read_text())
        for name, s in (rendered.get("services") or {}).items():
            e = s.get("environment") or {}
            if isinstance(e, list):
                e = dict(x.split("=", 1) for x in e if "=" in x)
            envs.append((f"compose service {name}", {str(k): str(v) for k, v in e.items()}))
    except (OSError, ValueError, AttributeError):
        pass
    out = []
    for where, kv in envs:
        users = [(k, v) for k, v in kv.items() if re.search(ADMIN_USER_VARS, k.upper()) and v]
        passes = [(k, v) for k, v in kv.items() if re.search(ADMIN_PASS_VARS, k.upper()) and v]
        for uk, uv in users:
            for pk, pv in passes:
                # pair by shared prefix (GF_SECURITY_ADMIN_USER / GF_SECURITY_ADMIN_PASSWORD), else any pair
                pre_u = re.sub(r"_?(USER|USERNAME|LOGIN|EMAIL|NAME|ACCOUNT)$", "", uk.upper()); pre_p = re.sub(r"_?(PASS|PASSWORD|PW|PASSWD)$", "", pk.upper())
                score = 0 if pre_u == pre_p else 1
                out.append((score, uv, pv, f"{where}: {uk}/{pk}"))
    out.sort(key=lambda x: x[0])
    seen, res = set(), []
    for _, u, p, w in out:
        if (u, p) not in seen:
            seen.add((u, p)); res.append((u, p, w))
    return res


def _exec_account(app: str, part: dict, log) -> tuple[str, str, str] | None:
    """Class exec: the recipe says how to create an account inside the container. Runs it once."""
    acc = part.get("account")
    if not isinstance(acc, dict) or acc.get("kind") != "exec" or not isinstance(acc.get("command"), list):
        return None
    username = str(acc.get("username") or ACCOUNT_USER)
    password = str(acc.get("password") or secrets.token_hex(PASSWORD_HEX))
    cmd = [str(c).replace("{username}", username).replace("{password}", password) for c in acc["command"]]
    project = "atta-" + safe_id(app)
    if part.get("kind") == "compose":
        rendered = WORK / safe_id(app) / "compose.rendered.json"
        service = acc.get("service") or part.get("service") or "app"
        full = ["docker", "compose", "-p", project, "-f", str(rendered), "exec", "-T", service] + cmd
    else:
        full = ["docker", "exec", project] + cmd
    try:
        r = subprocess.run(full, capture_output=True, text=True, timeout=180)
    except Exception as e:
        log(f"  account exec failed to run: {e}"); return None
    if r.returncode != 0:
        log(f"  account exec exit {r.returncode}: {(r.stdout + r.stderr)[-300:]}"); return None
    return username, password, "recipe account.exec"


# ------------------------------------------------------------------ browser actions
def _click_words(page, words: str) -> bool:
    rx = re.compile(words, re.I)
    for tag in ("a", "button", '[role="button"]'):
        loc = page.locator(tag, has_text=rx).first
        try:
            if loc.count():
                loc.click(); _settle(page); return True
        except Exception:
            continue
    return False


def _reach_login(page, url: str) -> bool:
    page.goto(url); _settle(page)
    if _has_login(page): return True
    if _click_words(page, LOGIN_WORDS) and _has_login(page): return True
    for path in ("/login", "/signin", "/auth/login", "/users/sign_in", "/user/login", "/admin", "/admin/login", "/ghost/"):
        try:
            page.goto(url.rstrip("/") + path); _settle(page)
        except Exception:
            continue
        if _has_login(page): return True
    page.goto(url); _settle(page)
    return _has_login(page)


def _fill_login(page, username: str, password: str) -> bool:
    u = None
    for sel in ('input[autocomplete="username"]', 'input[type="email"]', 'input[name*="user" i]', 'input[name*="email" i]',
                'input[name*="login" i]', 'input[id*="user" i]', 'input[id*="email" i]', 'input[type="text"]'):
        loc = page.locator(sel).first
        if loc.count(): u = loc; break
    pw = page.locator('input[type="password"]').first
    if u is None or not pw.count(): return False
    try:
        u.fill(username); pw.fill(password)
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        import system_watcher as w
        sub = w._login_submit(page, pw)          # the same control the watcher will press
        if sub.count(): sub.click()
        else: pw.press("Enter")
        try: page.wait_for_selector('input[type="password"]', state="detached", timeout=SETTLE_MS)
        except Exception: pass
    except Exception:
        return False
    _settle(page)
    return True


def _login_worked(page, before_url: str) -> bool:
    if _has_login(page): return False
    return True


def _markers_present(page) -> set:
    out = set()
    for sel in POST_LOGIN_MARKERS:
        try:
            if page.locator(sel).count(): out.add(sel)
        except Exception:
            continue
    return out


def _derive_expectation(before_text: str, before_url: str, before_markers: set, page) -> dict:
    """What logged-in looks like, from the REAL post-login page, in order of strength:
      1. a line of text that appeared and says log out / sign out (the surest sign of a session)
      2. a known post-login marker present now and absent on the login page
      3. the URL path changed
      4. any stable line of text that appeared (letters only, no digits)
    Nothing here is app-specific; everything is observed."""
    after_text = _text(page)
    b = set(l.strip() for l in before_text.splitlines())
    for l in after_text.splitlines():
        l = l.strip()
        if l and l not in b and re.search(r"^(log ?out|sign ?out|logout)$", l, re.I):
            return {"expected_text": l}
    for sel in POST_LOGIN_MARKERS:
        if sel in before_markers: continue
        try:
            if page.locator(sel).count():
                return {"expected_selector": sel}
        except Exception:
            continue
    a, bpath = urlparse(before_url).path.rstrip("/"), urlparse(page.url).path.rstrip("/")
    if bpath and bpath != a:
        return {"expected_url_contains": bpath}
    new = _new_text(before_text, after_text)
    if new:
        return {"expected_text": new}
    return {}


def _try_signup(page, url: str, log, username: str | None = None, email: str | None = None) -> tuple[str, str] | None:
    """Class signup: a register / first-run form. Fills every field a new account needs. If the app says
    the name is taken (an account from an earlier run whose store was lost), tries once more with a
    unique suffix."""
    username = username or ACCOUNT_USER
    email = email or ACCOUNT_EMAIL
    page.goto(url); _settle(page)
    if not (page.locator('input[autocomplete="new-password"]').count() or page.locator('input[type="password"]').count() >= 2):
        if not _click_words(page, SIGNUP_WORDS):
            for path in ("/signup", "/register", "/users/sign_up", "/auth/register", "/setup", "/install", "/ghost/#/setup", "/user/sign_up"):
                try:
                    page.goto(url.rstrip("/") + path); _settle(page)
                except Exception:
                    continue
                if page.locator('input[type="password"]').count(): break
    if not page.locator('input[type="password"]').count():
        return None
    password = secrets.token_hex(PASSWORD_HEX) + "Aa1!"
    try:
        # setup wizards hide the account section in collapsed blocks: open them all
        page.evaluate("document.querySelectorAll('details:not([open])').forEach(d => d.open = true)")
        # a wizard that offers an embedded database (SQLite / built-in) gets it: nothing to provision.
        # Native <select>s first, then custom dropdowns (an item carrying data-value inside a .dropdown).
        for sel in page.locator("form select:visible").all():
            try:
                opts = sel.locator("option").all()
                pick = next((o.get_attribute("value") for o in opts if re.search(EMBEDDED_DB_WORDS, (o.inner_text() or "") + " " + (o.get_attribute("value") or ""), re.I)), None)
                if pick is not None:
                    sel.select_option(pick); _settle(page, 2000)
            except Exception:
                continue
        for item in page.locator("form [data-value]").all():
            try:
                if not re.search(EMBEDDED_DB_WORDS, (item.get_attribute("data-value") or "") + " " + (item.inner_text() or ""), re.I): continue
                dd = item.locator("xpath=ancestor::*[contains(@class,'dropdown')][1]")
                if dd.count(): dd.first.click(); page.wait_for_timeout(300)
                item.click(); _settle(page, 2000); break
            except Exception:
                continue
        for inp in page.locator("form input:visible, input:visible").all():
            try:
                t = (inp.get_attribute("type") or "text").lower()
                name = " ".join(filter(None, [inp.get_attribute("name"), inp.get_attribute("id"), inp.get_attribute("placeholder"), inp.get_attribute("autocomplete")])).lower()
                if t in ("hidden", "submit", "button", "file", "radio", "range", "color", "number", "date"): continue
                if t == "checkbox":
                    # only consent boxes (terms / agree / accept) or ones the form marks required; never
                    # feature switches (a setup page's "enable mailer" would change the app, not the account)
                    if inp.get_attribute("required") is not None or re.search(r"terms|agree|accept|consent|privacy", name):
                        inp.check()
                    continue
                if t == "password": inp.fill(password)
                elif t == "email" or "email" in name: inp.fill(email)
                elif inp.input_value(): continue                      # pre-filled: the app's own default, keep it
                elif any(k in name for k in ("full", "display", "first", "last")): inp.fill(ACCOUNT_NAME)
                elif any(k in name for k in ("user", "login", "handle", "nick", "account", "name")): inp.fill(username)
                elif any(k in name for k in ("title", "org", "company", "workspace", "site", "blog")): inp.fill(ACCOUNT_NAME)
            except Exception as e:
                log(f"  signup field {name!r}: {type(e).__name__}")
        sub = None
        rx = re.compile(SUBMIT_WORDS, re.I)
        for c in (page.locator('form button[type="submit"],form input[type="submit"]'),
                  page.locator('form button, form [role="button"]', has_text=rx),
                  page.locator("button", has_text=rx),
                  page.locator('button[type="submit"],input[type="submit"]')):
            if c.count(): sub = c.last; break      # the submit is the LAST such control on a wizard, never an icon toggle
        if sub is not None: sub.click()
        else: page.keyboard.press("Enter")
    except Exception as e:
        log(f"  signup form: {type(e).__name__}: {e}"); return None
    _settle(page)
    txt = _text(page).lower()
    if re.search(r"already (exists|taken|registered|in use)|is taken", txt) and username == ACCOUNT_USER:
        tag = secrets.token_hex(3)
        log(f"  signup: {username!r} already exists in the app; trying {username}-{tag}")
        return _try_signup(page, url, log, f"{username}-{tag}", email.replace("@", f"+{tag}@", 1))
    if re.search(r"already (exists|taken|registered|in use)|is taken|invalid|error|required|must be", txt) and _has_login(page):
        return None
    return username, password


def _new_text(before: str, after: str) -> str | None:
    """A line of text that appeared after the click and was not there before: stable-looking only
    (letters, no digits, 4..60 chars), so a clock or a counter never becomes the expectation."""
    b = set(l.strip() for l in before.splitlines())
    for l in after.splitlines():
        l = l.strip()
        if l and l not in b and 4 <= len(l) <= 60 and not re.search(r"\d", l) and re.search(r"[A-Za-z]{3}", l):
            return l
    return None


def _smoke_contract(page, url: str) -> dict | None:
    """Class no-login: click a real control and expect the real result. Links first (a URL change is
    the strongest proof), then buttons/tabs (proof = text that appeared)."""
    page.goto(url); _settle(page)
    origin = urlparse(page.url)
    tried = 0
    for a in page.locator("a[href]").all():
        href = a.get_attribute("href") or ""
        if re.search(SMOKE_SKIP, href, re.I): continue
        p = urlparse(href)
        if p.netloc and p.netloc != origin.netloc: continue
        path = p.path.rstrip("/")
        if not path or path == origin.path.rstrip("/"): continue
        tried += 1
        if tried > SMOKE_TRIES: break
        sel = f'a[href="{href}"]'
        try:
            before = page.url
            page.locator(sel).first.click(); _settle(page)
            if path in page.url and page.url != before:
                page.goto(url); _settle(page)
                return {"type": "smoke", "selector": sel, "expected_url_contains": path, "wait_ms": SETTLE_MS}
        except Exception:
            pass
        try: page.goto(url); _settle(page)
        except Exception: pass
    tried = 0
    for i in range(page.locator("button, [role=button], [role=tab]").count()):
        if tried >= SMOKE_TRIES: break
        loc = page.locator("button, [role=button], [role=tab]").nth(i)
        try:
            label = (loc.inner_text(timeout=1000) or "").strip()
            if not label or re.search(r"log ?in|sign ?in|log ?out|delete|remove|notification", label, re.I): continue
            tried += 1
            sel = f'button:has-text("{label}"), [role=button]:has-text("{label}"), [role=tab]:has-text("{label}")'
            before_url, before_text = page.url, _text(page)
            loc.click(); _settle(page)
            after_url, after_text = page.url, _text(page)
            if after_url != before_url and urlparse(after_url).path.rstrip("/") and urlparse(after_url).path != urlparse(before_url).path:
                page.goto(url); _settle(page)
                return {"type": "smoke", "selector": sel, "expected_url_contains": urlparse(after_url).path.rstrip("/"), "wait_ms": SETTLE_MS}
            new = _new_text(before_text, after_text)
            if new:
                page.goto(url); _settle(page)
                return {"type": "smoke", "selector": sel, "expected_text": new, "wait_ms": SETTLE_MS}
        except Exception:
            pass
        try: page.goto(url); _settle(page)
        except Exception: pass
    return None


# ------------------------------------------------------------------ the author
def author(app: str, url: str, part: dict | None = None, log=_log) -> dict:
    """Returns {"journey": <contract or None>, "account": {"class":..,"where":..}, "code": <None or CODE>, "detail": str}.
    Never returns a secret; the contract references the store."""
    part = part or {}
    aid = safe_id(app)
    # 1. a supplied contract on the shelf wins (a person or the self-healer wrote it)
    supplied = RECIPES / f"{aid}.journey.json"
    if supplied.is_file():
        try:
            spec = json.loads(supplied.read_text())
            if isinstance(spec, dict) and spec.get("type") in ("login", "smoke"):
                return _dry_run(aid, url, spec, {"class": "supplied", "where": str(supplied)}, log)
        except ValueError:
            pass
    try:
        b = Browser()
    except Exception as e:
        return _out(None, {"class": None}, "BROWSER_UNAVAILABLE", f"{type(e).__name__}: {e}")
    try:
        page = b.page()
        try:
            login = _reach_login(page, url)
        except Exception as e:
            return _out(None, {"class": None}, "APP_UNREACHABLE", f"{type(e).__name__}: {e}")
        if not login:
            spec = _smoke_contract(page, url)
            if not spec:
                return _out(None, {"class": "none"}, "SMOKE_NO_CONTROL", "no login form and no internal link that changes the page")
            return _dry_run(aid, url, spec, {"class": "none", "where": "no login: smoke journey"}, log, b)
        login_url = page.url
        before_text = _text(page)
        before_markers = _markers_present(page)
        candidates: list[tuple[str, str, str]] = []
        stored = load_account(aid)
        if stored: candidates.append((stored["username"], stored["password"], "stored"))
        candidates += [(u, p, "env_admin: " + w) for u, p, w in _env_pairs(app, part)]
        got = _exec_account(app, part, log)
        if got: candidates.append(got)
        tried = []
        for username, password, where in candidates:
            page = b.fresh()
            try:
                _reach_login(page, url)
                if not _fill_login(page, username, password):
                    tried.append(f"{where}: no fields"); continue
                if _login_worked(page, login_url):
                    exp = _derive_expectation(before_text, login_url, before_markers, page)
                    if not exp:
                        tried.append(f"{where}: logged in but no post-login marker or URL change"); continue
                    if where != "stored":
                        save_account(aid, username, password, where)
                    spec = {"type": "login", "username": f"store:{aid}", "password": f"store:{aid}", "wait_ms": SETTLE_MS, **exp}
                    return _dry_run(aid, url, spec, {"class": where.split(":")[0], "where": where}, log, b)
                tried.append(f"{where}: login refused")
                if where == "stored":
                    forget_account(aid)   # the app's data was reset since; make a new one below
            except Exception as e:
                tried.append(f"{where}: {type(e).__name__}: {e}")
        # signup / first-run form
        try:
            page = b.fresh()
            got = _try_signup(page, url, log)
            if got:
                username, password = got
                # a setup wizard may restart the app: wait for it to answer again before logging in
                page = b.fresh()
                if not _wait_up(page, url, RESTART_WAIT_S):
                    tried.append(f"signup: form submitted, then the app stopped answering for {RESTART_WAIT_S}s")
                else:
                    _reach_login(page, url)
                    before_text = _text(page); before_markers = _markers_present(page); login_url = page.url
                    if _fill_login(page, username, password) and _login_worked(page, login_url):
                        exp = _derive_expectation(before_text, login_url, before_markers, page)
                        if exp:
                            save_account(aid, username, password, "signup")
                            spec = {"type": "login", "username": f"store:{aid}", "password": f"store:{aid}", "wait_ms": SETTLE_MS, **exp}
                            return _dry_run(aid, url, spec, {"class": "signup", "where": "sign-up / first-run form"}, log, b)
                        tried.append("signup: account created, logged in, but no post-login marker or URL change")
                    else:
                        tried.append("signup: form submitted but the new account could not log in")
            else:
                tried.append("signup: no sign-up or first-run form found")
        except Exception as e:
            tried.append(f"signup: {type(e).__name__}: {str(e).splitlines()[0][:160]}")
        return _out(None, {"class": None, "tried": tried}, "ACCOUNT_NO_STRATEGY",
                    "login required; no account class worked: " + "; ".join(tried))
    finally:
        b.close()


def _out(spec, account, code, detail):
    return {"journey": spec, "account": account, "code": code, "detail": detail}


def _dry_run(aid: str, url: str, spec: dict, account: dict, log, b: Browser | None = None) -> dict:
    """Run the authored contract ONCE with the watcher's own code, from a fresh session. Only a
    contract that passes here is handed to the watcher; the watcher then runs it again itself."""
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import system_watcher as w
    own = b is None
    if own:
        try: b = Browser()
        except Exception as e: return _out(None, account, "BROWSER_UNAVAILABLE", f"{type(e).__name__}: {e}")
    try:
        page = b.fresh()
        try:
            if spec.get("type") == "login": _reach_login(page, url)
            else: page.goto(url); _settle(page)
        except Exception as e:
            return _out(None, account, "APP_UNREACHABLE", f"{type(e).__name__}: {e}")
        code, detail = w.run_user_journey(page, spec, url)
        if code:
            return _out(None, account, "AUTHORED_BUT_FAILED:" + code, detail)
        log(f"  journey authored ({account.get('class')}): {detail}")
        return _out(spec, account, None, detail)
    finally:
        if own: b.close()


# ------------------------------------------------------------------ the fleet report
def journey_report(results: list[dict] | None = None, out: Path | None = None) -> str:
    """Every app's journey outcome, grouped by CODE, so the fix is written per class."""
    if results is None:
        results = []
        for f in sorted(RESULTS.glob("*.json")):
            try: results.append(json.loads(f.read_text()))
            except (OSError, ValueError): pass
    groups: dict[str, list] = {}
    for r in results:
        s6 = (r.get("stages") or {}).get("6 CLEAN") or {}
        ja = r.get("journey_author") or {}
        if s6.get("status") == "OK" and s6.get("journey"):
            key = "PASSED:" + str(s6["journey"].get("type"))
        elif ja.get("code"):
            key = ja["code"]
        elif s6.get("status") == "FAIL":
            key = str(s6.get("code") or "FAIL")
        elif not r.get("runner", {}).get("started", True):
            key = "NOT_STARTED"
        else:
            key = "NOT_REACHED:" + str(r.get("broken_at") or "")
        groups.setdefault(key, []).append((r.get("app"), (ja.get("account") or {}).get("class"), ja.get("detail") or s6.get("detail") or ""))
    lines = ["# Journeys: every app, grouped by outcome code", "",
             f"{len(results)} apps. Fix by CODE, never by app name: one fix per row below clears every app under it.", ""]
    for key in sorted(groups, key=lambda k: (not k.startswith("PASSED"), -len(groups[k]), k)):
        rows = groups[key]
        lines.append(f"## {key}  ({len(rows)})")
        for app, cls, detail in sorted(rows):
            lines.append(f"- {app}" + (f"  [{cls}]" if cls else "") + (f" — {detail[:200]}" if detail else ""))
        lines.append("")
    text = "\n".join(lines)
    out = out or (ROOT / "state" / "checklists" / "journeys.md")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text)
    return text


if __name__ == "__main__":
    if len(sys.argv) >= 2 and sys.argv[1] == "--report":
        print(journey_report())
    elif len(sys.argv) >= 3:
        r = author(sys.argv[1], sys.argv[2])
        print(json.dumps(r, indent=2))
        sys.exit(0 if r["journey"] else 1)
    else:
        print(__doc__); sys.exit(2)
