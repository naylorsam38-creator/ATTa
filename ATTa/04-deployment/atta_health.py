#!/usr/bin/env python3
"""ATTa-specific health checks (v117). Used by bootstrap.sh's gate, ADM (deployd) and the local launcher.

A deployment is verified only when ATTa itself answers, on the port .env names, through the real reverse proxy:

  gateway   GET http://HOST:PORT/health?challenge=<random> -> 200 JSON whose proof matches this installation's
            secret (atta_identity), service app-builder-gateway, and (if asked) the release that should be live.
  login     GET /login -> 200 with ATTa's login marker and form. Never nginx's welcome/test page.
  proxy     both of the above again THROUGH nginx, with the scheme it should serve: https (certificate checked
            against the system trust store, for the real domain) or http; plain http must redirect to https
            once a certificate is installed.
  browser   a real headless Chromium opens /login through the proxy and finds the same marker.

Redirects are never followed: a redirect to anything else can't pass. Every failure is one line, no traceback.

  python3 atta_health.py --env /srv/app-builder/.env [--proxy-file STATE/proxy.json] [--direct] [--proxy]
                         [--browser] [--expect-release NAME] [--legacy] [--timeout 60]
Exit 0 = every requested check passed. The last line is always `HEALTH PASS ...` or `HEALTH FAIL ...`.
"""
from __future__ import annotations
import argparse, http.client, json, secrets, socket, ssl, sys, time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import atta_identity, envfile  # noqa: E402

# Pages that mean "a web server is here, but it is not ATTa". Checked only to give a clear reason: the proof is
# what actually decides.
FOREIGN_PAGES = ("Welcome to nginx", "Test Page for the Nginx", "Test Page for the HTTP Server",
                 "It works!", "Apache2 Ubuntu Default Page", "Directory listing for")
LEGACY_LOGIN = "<h1>APP Builder</h1>"
LOGIN_FORM = "<form method=post action=/login>"


class CheckFailed(Exception):
    pass


class _SNIConnection(http.client.HTTPSConnection):
    """Connect to `connect_host` but present and verify the certificate for `server_name` (the real domain)."""

    def __init__(self, connect_host, port, server_name, timeout, cafile=None):
        ctx = ssl.create_default_context(cafile=cafile)
        super().__init__(connect_host, port, timeout=timeout, context=ctx)
        self._server_name = server_name
        self._ctx = ctx

    def connect(self):
        sock = socket.create_connection((self.host, self.port), self.timeout)
        self.sock = self._ctx.wrap_socket(sock, server_hostname=self._server_name)


def _get(scheme, connect_host, port, path, host_header, timeout, cafile=None):
    """(status, headers{lower: value}, body text) — one request, no redirects followed."""
    if scheme == "https":
        conn = _SNIConnection(connect_host, port, host_header, timeout, cafile)
    elif scheme == "http":
        conn = http.client.HTTPConnection(connect_host, port, timeout=timeout)
    else:
        raise CheckFailed(f"unknown scheme {scheme!r}")
    try:
        conn.request("GET", path, headers={"Host": host_header if port in (80, 443) else f"{host_header}:{port}",
                                           "User-Agent": "atta-health/1", "Accept": "*/*"})
        r = conn.getresponse()
        body = r.read(256 * 1024).decode("utf-8", "replace")
        return r.status, {k.lower(): v for k, v in r.getheaders()}, body
    except ssl.SSLCertVerificationError as e:
        raise CheckFailed(f"TLS certificate for {host_header} not valid: {e.verify_message}")
    except (OSError, http.client.HTTPException) as e:
        raise CheckFailed(f"no answer from {scheme}://{connect_host}:{port}{path} ({type(e).__name__}: {e})")
    finally:
        conn.close()


def _foreign(body):
    return next((m for m in FOREIGN_PAGES if m in body), None)


def check_identity(scheme, connect_host, port, host_header, secret, *, expect_release=None, legacy=False,
                   timeout=5.0, cafile=None):
    """Raise CheckFailed unless THIS ATTa installation answers /health here. Returns what it reported."""
    where = f"{scheme}://{host_header}:{port}"
    if legacy:
        status, hdr, body = _get(scheme, connect_host, port, "/health", host_header, timeout, cafile)
        if status != 200 or body.strip() != "OK":
            raise CheckFailed(f"{where}/health answered {status} {(_foreign(body) or body.strip()[:60])!r}, not OK")
        return {"legacy": True}
    challenge = secrets.token_hex(24)
    status, hdr, body = _get(scheme, connect_host, port, f"/health?challenge={challenge}", host_header, timeout, cafile)
    if status != 200:
        f = _foreign(body)
        raise CheckFailed(f"{where}/health answered {status}" + (f" ({f!r}: not ATTa)" if f else ""))
    try:
        d = json.loads(body)
    except ValueError:
        f = _foreign(body)
        raise CheckFailed(f"{where}/health is not ATTa's: " + (f"{f!r} page" if f else f"answer {body.strip()[:60]!r}"))
    if d.get("service") != atta_identity.SERVICE or hdr.get("x-atta-service") != atta_identity.SERVICE:
        raise CheckFailed(f"{where}/health is a different service ({str(d.get('service'))[:40]!r})")
    if not atta_identity.verify(secret, challenge, d.get("proof")):
        raise CheckFailed(f"{where}/health: wrong proof — another ATTa installation or an impostor answers here")
    if expect_release and d.get("release") != expect_release:
        raise CheckFailed(f"{where}/health: release {d.get('release')!r} answers, expected {expect_release!r} "
                          "(an old process may still hold the port)")
    return d


def check_login(scheme, connect_host, port, host_header, *, legacy=False, timeout=5.0, cafile=None):
    where = f"{scheme}://{host_header}:{port}/login"
    status, hdr, body = _get(scheme, connect_host, port, "/login", host_header, timeout, cafile)
    if status != 200:
        raise CheckFailed(f"{where} answered {status}" + (f" -> {hdr['location']}" if "location" in hdr else ""))
    f = _foreign(body)
    if f:
        raise CheckFailed(f"{where} shows {f!r}, not ATTa's login")
    marker = LEGACY_LOGIN if legacy else atta_identity.LOGIN_MARKER
    if marker not in body or LOGIN_FORM not in body:
        raise CheckFailed(f"{where} is not ATTa's login page")


def check_redirect_to_https(connect_host, host_header, timeout=5.0, http_port=80, https_port=443):
    status, hdr, _ = _get("http", connect_host, http_port, "/login", host_header, timeout)
    loc = hdr.get("location", "")
    want = f"https://{host_header}/" if https_port == 443 else f"https://{host_header}:{https_port}/"
    if status not in (301, 302, 307, 308) or not loc.startswith(want):
        raise CheckFailed(f"http://{host_header}/login answered {status} {loc!r}: plain HTTP must redirect to HTTPS")


def check_browser(url, host_header, connect_host, *, legacy=False, timeout=60.0):
    """Real Chromium through the proxy. Clean one-line errors, never a traceback."""
    try:
        from playwright.sync_api import sync_playwright, Error as PWError
    except ImportError as e:
        raise CheckFailed(f"browser check: Playwright is not installed ({e})")
    args = []
    if host_header not in ("127.0.0.1", "localhost") and connect_host in ("127.0.0.1", "::1"):
        args.append(f"--host-resolver-rules=MAP {host_header} {connect_host}")
    try:
        with sync_playwright() as p:
            try:
                browser = p.chromium.launch(headless=True, args=args)
            except PWError as e:
                first = str(e).strip().splitlines()[0] if str(e).strip() else type(e).__name__
                missing = "missing dependencies" in str(e) or "shared libraries" in str(e)
                raise CheckFailed("browser cannot launch: " + ("system libraries are missing (install the browser "
                                  "dependencies for this OS); " if missing else "") + first[:300])
            try:
                page = browser.new_page()
                resp = page.goto(url, wait_until="load", timeout=int(timeout * 1000))
                if resp is None or resp.status != 200:
                    raise CheckFailed(f"browser: {url} answered {resp.status if resp else 'nothing'}")
                if page.title() != "Login":
                    raise CheckFailed(f"browser: {url} has title {page.title()[:60]!r}, not ATTa's login")
                if not legacy and page.locator('meta[name="atta-page"][content="login"]').count() != 1:
                    raise CheckFailed(f"browser: {url} lacks ATTa's login marker")
                if page.locator('form[action="/login"] input[type=password]').count() != 1:
                    raise CheckFailed(f"browser: {url} has no login form")
            finally:
                browser.close()
    except CheckFailed:
        raise
    except Exception as e:  # anything Playwright raises past launch: one line
        raise CheckFailed(f"browser: {type(e).__name__}: {str(e).strip().splitlines()[0][:300] if str(e).strip() else ''}")


def check_launch(timeout=60.0):
    """Can a real headless Chromium start here at all (before there is anything to point it at)?"""
    try:
        from playwright.sync_api import sync_playwright, Error as PWError
    except ImportError as e:
        raise CheckFailed(f"Playwright is not installed ({e})")
    try:
        with sync_playwright() as p:
            try:
                browser = p.chromium.launch(headless=True)
            except PWError as e:
                first = str(e).strip().splitlines()[0] if str(e).strip() else type(e).__name__
                missing = "missing dependencies" in str(e) or "shared libraries" in str(e)
                raise CheckFailed(("system libraries are missing (install the browser dependencies for this OS); "
                                   if missing else "") + first[:300])
            try:
                page = browser.new_page()
                page.goto("data:text/html,<title>ATTa browser gate</title>", wait_until="load",
                          timeout=int(timeout * 1000))
                if page.title() != "ATTa browser gate":
                    raise CheckFailed("the browser started but did not render a page")
            finally:
                browser.close()
    except CheckFailed:
        raise
    except Exception as e:
        raise CheckFailed(f"{type(e).__name__}: {str(e).strip().splitlines()[0][:300] if str(e).strip() else ''}")


def settings(env_file):
    """(host, port, secret) from .env, parsed as data."""
    try:
        vals, _ = envfile.load(str(env_file))
    except (OSError, UnicodeDecodeError, envfile.EnvFileError) as e:
        raise CheckFailed(f"cannot read {env_file}: {e}")
    try:
        port = int(vals.get("APP_BUILDER_PORT") or 8787)
        assert 0 < port < 65536
    except (ValueError, AssertionError):
        raise CheckFailed(f"{env_file}: APP_BUILDER_PORT is not a port number")
    host = vals.get("APP_BUILDER_HOST") or "127.0.0.1"
    if host in ("0.0.0.0", "::", ""):
        host = "127.0.0.1"
    secret = vals.get("APP_BUILDER_SESSION_SECRET") or ""
    if not secret:
        raise CheckFailed(f"{env_file}: APP_BUILDER_SESSION_SECRET is empty; the gateway cannot prove who it is")
    return host, port, secret


def proxy_target(proxy_file):
    """What nginx should look like, as bootstrap.sh last rendered it: {scheme, host, port, connect, redirect}."""
    d = json.loads(Path(proxy_file).read_text())
    scheme = d.get("scheme")
    if scheme not in ("http", "https"):
        raise CheckFailed(f"{proxy_file}: scheme must be http or https")
    return {"scheme": scheme, "host": str(d.get("host") or "127.0.0.1"), "port": int(d.get("port") or
            (443 if scheme == "https" else 80)), "connect": str(d.get("connect") or "127.0.0.1"),
            "redirect": bool(d.get("redirect_http")), "cafile": d.get("cafile"),
            "http_port": int(d.get("http_port") or 80)}


def run_checks(env_file, *, proxy_file=None, direct=True, proxy=False, browser=False, expect_release=None,
               legacy=False, timeout=60.0, interval=1.0, log=print):
    """Retry until every requested check passes or `timeout` runs out. Returns (ok, [lines])."""
    deadline = time.monotonic() + timeout
    lines = []
    while True:
        lines = []
        try:
            if direct or proxy:
                host, port, secret = settings(env_file)
            if direct:
                d = check_identity("http", host, port, host, secret, expect_release=expect_release, legacy=legacy)
                check_login("http", host, port, host, legacy=legacy)
                lines.append(f"PASS gateway http://{host}:{port} " + ("(legacy release, no identity proof)" if legacy
                             else f"release {d.get('release')} version {d.get('version')} instance {d.get('instance')}"))
            if proxy or browser:
                if not proxy_file:
                    raise CheckFailed("no proxy description given (--proxy-file)")
                t = proxy_target(proxy_file)
                if proxy:
                    check_identity(t["scheme"], t["connect"], t["port"], t["host"], secret,
                                   expect_release=expect_release, legacy=legacy, cafile=t["cafile"])
                    check_login(t["scheme"], t["connect"], t["port"], t["host"], legacy=legacy, cafile=t["cafile"])
                    if t["redirect"]:
                        check_redirect_to_https(t["connect"], t["host"], http_port=t["http_port"], https_port=t["port"])
                    lines.append(f"PASS proxy {t['scheme']}://{t['host']}:{t['port']} (via {t['connect']})"
                                 + (" + http->https redirect" if t["redirect"] else ""))
                if browser:
                    url = f"{t['scheme']}://{t['host']}" + ("" if t["port"] in (80, 443) else f":{t['port']}") + "/login"
                    check_browser(url, t["host"], t["connect"], legacy=legacy)
                    lines.append(f"PASS browser {url}")
            return True, lines
        except CheckFailed as e:
            last = f"FAIL {e}"
        except (OSError, ValueError) as e:
            last = f"FAIL {type(e).__name__}: {e}"
        if time.monotonic() >= deadline:
            return False, lines + [last]
        time.sleep(interval)


def main(argv=None):
    ap = argparse.ArgumentParser(description="ATTa-specific health checks")
    ap.add_argument("--env", help="the .env file (port, host and the secret the proof is keyed on)")
    ap.add_argument("--launch-only", action="store_true", help="only check that a real Chromium starts here")
    ap.add_argument("--proxy-file", help="proxy.json written by bootstrap.sh (scheme/host/port nginx serves)")
    ap.add_argument("--direct", action="store_true", help="check the gateway on its own port")
    ap.add_argument("--proxy", action="store_true", help="check through nginx")
    ap.add_argument("--browser", action="store_true", help="real Chromium through nginx")
    ap.add_argument("--expect-release", help="release folder name that must be the one answering")
    ap.add_argument("--legacy", action="store_true", help="the release predates identity proofs (body OK only)")
    ap.add_argument("--timeout", type=float, default=60.0)
    a = ap.parse_args(argv)
    if a.launch_only:
        try:
            check_launch(timeout=a.timeout)
        except CheckFailed as e:
            print(f"HEALTH FAIL browser cannot launch: {e}")
            return 1
        print("HEALTH PASS browser launches")
        return 0
    if not (a.direct or a.proxy or a.browser):
        a.direct = True
    if (a.direct or a.proxy) and not a.env:
        ap.error("--env is required for --direct/--proxy (the identity proof is keyed on its secret)")
    ok, lines = run_checks(a.env, proxy_file=a.proxy_file, direct=a.direct, proxy=a.proxy, browser=a.browser,
                           expect_release=a.expect_release, legacy=a.legacy, timeout=a.timeout)
    for l in lines:
        print(l)
    print("HEALTH PASS" if ok else f"HEALTH {lines[-1] if lines else 'FAIL'}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
