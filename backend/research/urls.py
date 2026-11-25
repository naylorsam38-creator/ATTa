"""URL canonicalisation, domain restrictions and planning-time destination checks (§10, §14).

The authoritative network enforcement (DNS resolution, pinned connections, redirects, sizes) happens where the
socket is opened: the isolated research.web handler (capabilities/research_web/netguard.py). This module refuses
obviously unsafe or out-of-scope URLs BEFORE a task contract is ever written, so they never reach a worker's pool
(defence in depth). tests/research/test_url_security.py keeps both layers in agreement on a shared corpus."""
from __future__ import annotations

import ipaddress
import posixpath
import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from .errors import ResearchError, ResearchErrorCode

TRACKING_PARAMS = re.compile(r"^(utm_[a-z_]+|fbclid|gclid|dclid|msclkid|mc_cid|mc_eid|igshid|_hsenc|_hsmi|ref_src|"
                             r"spm|yclid|oly_[a-z_]+|vero_[a-z_]+)$", re.I)
BLOCKED_SUFFIXES = (".localhost", ".local", ".internal", ".lan", ".home", ".corp", ".intranet", ".arpa", ".onion")
BLOCKED_NAMES = frozenset({"localhost", "metadata", "metadata.google.internal", "instance-data", "kubernetes.default",
                           "kubernetes.default.svc"})
_NUMERIC = re.compile(r"^(0x[0-9a-f]+|[0-9]+)(\.(0x[0-9a-f]+|[0-9]+))*$")


def _host(raw: str) -> str:
    try:
        return raw.encode("idna").decode("ascii").lower().rstrip(".")
    except UnicodeError as exc:
        raise ResearchError(ResearchErrorCode.NETWORK_DENIED, f"invalid hostname: {exc}") from exc


def canonicalize_url(url: str) -> str:
    """Stable identity of a URL for deduplication. NOT evidence: content identity is the snapshot hash."""
    p = urlsplit(url.strip())
    scheme = p.scheme.lower()
    host = _host(p.hostname or "")
    port = p.port
    netloc = f"[{host}]" if ":" in host else host
    if port and not ((scheme == "https" and port == 443) or (scheme == "http" and port == 80)):
        netloc += f":{port}"
    path = posixpath.normpath(p.path) if p.path else "/"
    if p.path.endswith("/") and not path.endswith("/"):
        path += "/"
    if path == ".":
        path = "/"
    query = urlencode(sorted((k, v) for k, v in parse_qsl(p.query, keep_blank_values=True)
                             if not TRACKING_PARAMS.match(k)))
    return urlunsplit((scheme, netloc, path, query, ""))


def host_of(url: str) -> str:
    return _host(urlsplit(url).hostname or "")


def registrable_group(host: str) -> str:
    """Coarse ownership group for independence metadata: the last two labels (three for common 2nd-level ccTLDs).
    Deliberately simple and documented — it only flags 'possibly same publisher', it never decides truth."""
    labels = host.split(".")
    if len(labels) >= 3 and labels[-2] in ("co", "com", "org", "gov", "ac", "net", "edu") and len(labels[-1]) == 2:
        return ".".join(labels[-3:])
    return ".".join(labels[-2:])


def domain_matches(host: str, pattern: str) -> bool:
    pattern = pattern.lower()
    if pattern.startswith("*."):
        base = pattern[2:]
        return host == base or host.endswith("." + base)
    return host == pattern or host.endswith("." + pattern)


def precheck_url(url: str, loopback_ports_for_tests: frozenset[int] = frozenset()) -> str:
    """Planning-time structural check. Returns the canonical URL or raises NETWORK_DENIED.

    `loopback_ports_for_tests` is set only by test harnesses that run deterministic local HTTP fixtures; the
    production factory never passes it (and the production research.web package refuses loopback regardless)."""
    if not isinstance(url, str) or len(url) > 2048 or any(ord(c) < 0x21 or c == "\\" for c in url):
        raise ResearchError(ResearchErrorCode.NETWORK_DENIED, "URL missing, too long or contains control characters")
    try:
        p = urlsplit(url)
        p.port
    except ValueError as exc:
        raise ResearchError(ResearchErrorCode.NETWORK_DENIED, f"malformed URL: {exc}") from exc
    if p.scheme.lower() not in ("http", "https"):
        raise ResearchError(ResearchErrorCode.NETWORK_DENIED, f"scheme '{p.scheme}' is not permitted")
    if p.username is not None or p.password is not None or "@" in p.netloc:
        raise ResearchError(ResearchErrorCode.NETWORK_DENIED, "credentials in URLs are not permitted")
    host = _host(p.hostname or "")
    if not host:
        raise ResearchError(ResearchErrorCode.NETWORK_DENIED, "URL has no host")
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        ip = None
    test_loopback = ip is not None and ip.is_loopback and p.port in loopback_ports_for_tests
    if ip is not None:
        if not test_loopback and (not ip.is_global or ip.is_multicast or
                                  (ip.version == 6 and ip.ipv4_mapped and not ip.ipv4_mapped.is_global)):
            raise ResearchError(ResearchErrorCode.NETWORK_DENIED, f"destination {ip} is not a public address")
    else:
        if _NUMERIC.match(host) or host in BLOCKED_NAMES or host.endswith(BLOCKED_SUFFIXES) or "." not in host:
            raise ResearchError(ResearchErrorCode.NETWORK_DENIED, f"hostname '{host}' is not a public name")
    if p.port not in (None, 80, 443) and not test_loopback:
        raise ResearchError(ResearchErrorCode.NETWORK_DENIED, f"port {p.port} is not permitted")
    return canonicalize_url(url)


def check_scope(url: str, allowed: list[str], excluded: list[str], excluded_urls: list[str]) -> None:
    """Contract domain restrictions and excluded sources (raises NETWORK_DENIED)."""
    host = host_of(url)
    canon = canonicalize_url(url)
    if canon in {canonicalize_url(u) for u in excluded_urls}:
        raise ResearchError(ResearchErrorCode.NETWORK_DENIED, f"{canon} is an excluded source")
    if any(domain_matches(host, d) for d in excluded):
        raise ResearchError(ResearchErrorCode.NETWORK_DENIED, f"{host} is in an excluded domain")
    if allowed and not any(domain_matches(host, d) for d in allowed):
        raise ResearchError(ResearchErrorCode.NETWORK_DENIED, f"{host} is outside the allowed domains")
