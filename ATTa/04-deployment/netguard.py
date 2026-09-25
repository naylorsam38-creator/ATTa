#!/usr/bin/env python3
"""
netguard.py — v116: what the server itself may reach on behalf of an app or a user (review item #8).

Two ways ATTa fetches things for someone else:
  - a git address a user types on the Add page (and seed/catalogue repos): before v116 any https host at all,
    so the server could be pointed at internal services and, through them, the cloud metadata service;
  - documentation links found in an uploaded app's OWN README (upstream.py), opened by the server: an app
    could list http://169.254.169.254/... or http://127.0.0.1:.../ and have its answer cached as build input.

Now:
  check_repo_url()  git addresses: https only, host on the allow-list (APP_BUILDER_GIT_HOSTS), and never a
                    host that resolves to a private, loopback, link-local or otherwise non-public address.
  check_fetch_url() documentation pages: http/https on the standard ports, to public addresses only.
  safe_get()        fetches such a page, re-checking every redirect hop the same way.
  git_cmd()         every git network call: https only, no hooks, no submodules, no credential prompts.
The remaining gap (a public name that changes its DNS answer between the check and the connection) is closed
for AWS by bootstrap.sh requiring IMDSv2 tokens, which a GET-only request cannot obtain.
"""
from __future__ import annotations
import ipaddress, os, re, socket
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

# ===================== CONFIG — edit here, nothing below needs reading =====================
# Git hosts apps may be added from (comma separated). Add your own Gitea/GitLab host here if you use one.
DEFAULT_GIT_HOSTS = "github.com,gitlab.com,codeberg.org,bitbucket.org"
# Redirects followed when fetching a documentation page (each hop is checked).
MAX_REDIRECTS = 5
# ==========================================================================================

REPO_URL = re.compile(r"^https://[A-Za-z0-9.-]+/[A-Za-z0-9._~/-]+?(\.git)?/?$")


class Refused(ValueError):
    pass


def git_hosts() -> set[str]:
    return {h.strip().lower() for h in os.environ.get("APP_BUILDER_GIT_HOSTS", DEFAULT_GIT_HOSTS).split(",") if h.strip()}


def _addresses(host: str) -> list:
    try:
        infos = socket.getaddrinfo(host, None, proto=socket.IPPROTO_TCP)
    except (socket.gaierror, UnicodeError, OSError):
        return []
    out = []
    for info in infos:
        a = ipaddress.ip_address(info[4][0].split("%", 1)[0])
        if a.version == 6 and a.ipv4_mapped:
            a = a.ipv4_mapped
        out.append(a)
    return out


def _literal(host: str):
    try:
        a = ipaddress.ip_address(host.strip("[]"))
        return a.ipv4_mapped if a.version == 6 and a.ipv4_mapped else a
    except ValueError:
        return None


def is_public_host(host: str, *, unresolvable_ok: bool = False) -> bool:
    """True when every address the host resolves to is public (is_global). Unresolvable = False unless allowed."""
    lit = _literal(host)
    if lit is not None:
        return lit.is_global
    if host.lower() in ("localhost",) or host.lower().endswith((".localhost", ".local", ".internal")):
        return False
    addrs = _addresses(host)
    if not addrs:
        return unresolvable_ok
    return all(a.is_global for a in addrs)


def check_repo_url(url: str) -> str:
    """The git address, or Refused with a reason a person can act on."""
    url = (url or "").strip()
    if not REPO_URL.fullmatch(url):
        raise Refused("use an https address like https://github.com/owner/name")
    host = urlsplit(url).hostname or ""
    if host.lower() not in git_hosts():
        raise Refused(f"{host} is not an allowed git host ({', '.join(sorted(git_hosts()))}); an admin can add hosts "
                      "with APP_BUILDER_GIT_HOSTS")
    # an allowed NAME must still not lead inside the network (a hosts-file entry, a split-horizon DNS answer)
    if not is_public_host(host, unresolvable_ok=True):
        raise Refused(f"{host} resolves to a private or local address")
    return url


def check_fetch_url(url: str) -> str:
    parts = urlsplit(url or "")
    if parts.scheme not in ("http", "https"):
        raise Refused("only http/https pages are fetched")
    if parts.username or parts.password:
        raise Refused("addresses with credentials are not fetched")
    try:
        port = parts.port
    except ValueError:
        raise Refused("bad port")
    if port not in (None, 80, 443):
        raise Refused("only the standard web ports are fetched")
    if not parts.hostname or not is_public_host(parts.hostname):
        raise Refused(f"{parts.hostname or '(no host)'} is not a public address")
    return url


class _CheckedRedirects(HTTPRedirectHandler):
    max_redirections = MAX_REDIRECTS

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        check_fetch_url(newurl)          # raises Refused: the redirect is not followed
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def safe_get(url: str, headers: dict, timeout: float, max_bytes: int):
    """(content-type, body text) of a public page, or raises Refused / OSError."""
    check_fetch_url(url)
    opener = build_opener(_CheckedRedirects)
    with opener.open(Request(url, headers=headers), timeout=timeout) as r:
        return r.headers.get("Content-Type", ""), r.read(max_bytes).decode("utf-8", "replace")


GIT_ENV = {"GIT_TERMINAL_PROMPT": "0", "GIT_ASKPASS": "/bin/false", "SSH_ASKPASS": "/bin/false"}


def git_env(base: dict | None = None) -> dict:
    b = dict(base if base is not None else os.environ)
    keep = {k: b[k] for k in ("PATH", "HOME", "LANG", "HTTPS_PROXY", "HTTP_PROXY", "NO_PROXY", "https_proxy",
                              "http_proxy", "no_proxy", "GIT_SSL_CAINFO", "SSL_CERT_FILE") if k in b}
    keep.setdefault("PATH", "/usr/local/bin:/usr/bin:/bin")
    return {**keep, **GIT_ENV}


def git_cmd(*args: str) -> list[str]:
    """git with every network protocol but https off, no hooks, no submodules, no stored credentials."""
    return ["git", "-c", "protocol.allow=never", "-c", "protocol.https.allow=always", "-c", "core.hooksPath=/dev/null",
            "-c", "submodule.recurse=false", "-c", "credential.helper=", "-c", "core.askPass=/bin/false", *args]


def git_clone_cmd(url: str, dest, *opts: str) -> list[str]:
    return git_cmd("clone", "--no-recurse-submodules", *opts, "--", url, str(dest))
