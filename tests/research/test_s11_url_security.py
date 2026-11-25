"""Stage 11 retrieval security (§10): URL validation, SSRF / private-network / metadata denial, DNS rebinding, redirect
validation, response-size limits, timeouts and content types — against deterministic local HTTP fixtures.

The retrieval code under test is the real research.web netguard module (loaded here directly; in production it runs
only inside the isolated handler process). Loopback is reachable ONLY when the policy lists the fixture port, which
only a test copy of the package can do."""
from __future__ import annotations

import importlib.util
import socket
from pathlib import Path

import pytest

from backend.research.errors import ResearchError, ResearchErrorCode
from backend.research.urls import canonicalize_url, check_scope, precheck_url

PKG = Path(__file__).resolve().parents[2] / "capabilities" / "research_web"


def _load(name):
    spec = importlib.util.spec_from_file_location(f"s11_{name}", PKG / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


ng = _load("netguard")
PROD = _load("retrieval_policy").POLICY


def policy(**kw):
    return {**PROD, **kw}


DENIED_IPS = ["127.0.0.1", "127.255.255.254", "10.0.0.1", "172.16.5.4", "192.168.1.1", "169.254.169.254",
              "169.254.170.2", "100.100.100.200", "100.64.0.1", "0.0.0.0", "224.0.0.1", "255.255.255.255",
              "192.0.0.192", "198.18.0.1", "::1", "::", "fe80::1", "fc00::1", "fd00:ec2::254", "::ffff:127.0.0.1",
              "::ffff:10.0.0.1", "64:ff9b::7f00:1", "2002:7f00:1::", "ff02::1", "2001:db8::1"]
PUBLIC_IPS = ["93.184.216.34", "1.1.1.1", "8.8.8.8", "2606:4700:4700::1111"]


@pytest.mark.parametrize("ip", DENIED_IPS)
def test_private_loopback_metadata_and_special_addresses_denied(ip):
    assert ng.classify_ip(ip) is not None


@pytest.mark.parametrize("ip", PUBLIC_IPS)
def test_public_addresses_allowed(ip):
    assert ng.classify_ip(ip) is None


BAD_URLS = ["file:///etc/passwd", "ftp://example.com/x", "gopher://example.com/", "javascript:alert(1)",
            "data:text/html,hi", "http://user:pass@example.com/", "http://example.com@169.254.169.254/",
            "http://localhost/", "http://metadata.google.internal/", "http://foo.internal/", "http://printer.local/",
            "http://2130706433/", "http://0x7f.0.0.1/", "http://017700000001/", "http://127.1/",
            "http://[::1]/", "http://169.254.169.254/latest/meta-data/", "http://example.com:22/",
            "http://example.com:6379/", "http://intranet/", "http://exa mple.com/", "http://example.com\\@evil.com/",
            "", "http:///nohost"]


@pytest.mark.parametrize("url", BAD_URLS)
def test_unsafe_urls_refused_by_handler_and_planner(url):
    with pytest.raises(ng.RetrievalError) as e:
        ng.validate_url(url, PROD)
    assert e.value.code == "NETWORK_DENIED"
    with pytest.raises(ResearchError) as e2:          # the planning-time layer agrees on the same corpus
        precheck_url(url)
    assert e2.value.code is ResearchErrorCode.NETWORK_DENIED


def test_safe_url_normalised():
    scheme, host, port, path, norm = ng.validate_url("HTTPS://Example.COM/a/b?x=1#frag", PROD)
    assert (scheme, host, port, path) == ("https", "example.com", 443, "/a/b?x=1") and "#" not in norm
    assert precheck_url("https://example.com/a?utm_source=t&b=2") == "https://example.com/a?b=2"


def test_dns_rebinding_mixed_answers_denied():
    def resolver(host, port, *a):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", port)),
                (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", port))]
    with pytest.raises(ng.RetrievalError) as e:
        ng.resolve_public("rebind.example.com", 443, PROD, resolver)
    assert e.value.code == "NETWORK_DENIED"


def test_dns_resolving_to_private_denied_and_public_pinned():
    priv = lambda h, p, *a: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.1.2.3", p))]  # noqa: E731
    with pytest.raises(ng.RetrievalError):
        ng.resolve_public("internal.example.com", 443, PROD, priv)
    pub = lambda h, p, *a: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", p))]  # noqa: E731
    assert ng.resolve_public("example.com", 443, PROD, pub) == "93.184.216.34"


def test_error_codes_identical_between_layers():
    for code in (ng.NETWORK_DENIED, ng.SOURCE_UNAVAILABLE, ng.SOURCE_TOO_LARGE, ng.UNSUPPORTED_CONTENT):
        assert ResearchErrorCode(code)


def test_production_policy_denies_loopback_fixture(fx):
    """Private-network denial: the production policy refuses the local fixture outright (no socket opened)."""
    before = len(fx.hits)
    with pytest.raises(ng.RetrievalError) as e:
        ng.fetch(f"{fx.base}/page/a.html", PROD)
    assert e.value.code == "NETWORK_DENIED" and len(fx.hits) == before
    assert PROD["loopback_ports_allowed_for_tests"] == []


# ---- the rest uses a policy that admits ONLY the fixture port on loopback -------------------------------------
def test_fetch_ok_and_same_host_redirect(fx):
    p = policy(loopback_ports_allowed_for_tests=[fx.port])
    r = ng.fetch(f"{fx.base}/redirect-same", p)
    assert r["status"] == 200 and r["final_url"].endswith("/page/a.html") and r["redirects"][0]["status"] == 301
    assert r["content_type"] == "text/html" and b"Rivertown" in r["body"]


@pytest.mark.parametrize("path,code,fragment", [
    ("/redirect-metadata", "NETWORK_DENIED", "169.254.169.254"),
    ("/redirect-cross", "NETWORK_DENIED", "localhost"),
    ("/redirect-loop", "NETWORK_DENIED", "redirects"),
    ("/redirect-scheme", "NETWORK_DENIED", "scheme"),
])
def test_unsafe_redirects_refused(fx, path, code, fragment):
    p = policy(loopback_ports_allowed_for_tests=[fx.port])
    with pytest.raises(ng.RetrievalError) as e:
        ng.fetch(fx.base + path, p)
    assert e.value.code == code and fragment in str(e.value), str(e.value)


def test_response_size_limits(fx):
    p = policy(loopback_ports_allowed_for_tests=[fx.port], max_response_bytes=1024 * 1024,
               max_decompressed_bytes=2 * 1024 * 1024)
    for path in ("/big-declared", "/big-stream", "/gzip-bomb"):
        with pytest.raises(ng.RetrievalError) as e:
            ng.fetch(fx.base + path, p)
        assert e.value.code == "SOURCE_TOO_LARGE", (path, e.value)


def test_timeout(fx):
    p = policy(loopback_ports_allowed_for_tests=[fx.port], read_timeout_s=1.0, connect_timeout_s=1.0,
               total_timeout_s=2.0)
    with pytest.raises(ng.RetrievalError) as e:
        ng.fetch(f"{fx.base}/slow?s=5", p)
    assert e.value.code == "SOURCE_UNAVAILABLE" and e.value.extra.get("timeout") is True


def test_unsupported_content_and_http_errors(fx):
    p = policy(loopback_ports_allowed_for_tests=[fx.port])
    with pytest.raises(ng.RetrievalError) as e:
        ng.fetch(f"{fx.base}/binary", p)
    assert e.value.code == "UNSUPPORTED_CONTENT"
    with pytest.raises(ng.RetrievalError) as e:
        ng.fetch(f"{fx.base}/missing", p)
    assert e.value.code == "SOURCE_UNAVAILABLE" and e.value.extra["http_status"] == 404


def test_test_port_allowance_is_port_specific(fx):
    p = policy(loopback_ports_allowed_for_tests=[fx.port + 1 if fx.port < 65535 else fx.port - 1])
    with pytest.raises(ng.RetrievalError) as e:
        ng.fetch(f"{fx.base}/page/a.html", p)
    assert e.value.code == "NETWORK_DENIED"


def test_canonicalisation_and_scope():
    assert canonicalize_url("HTTP://Example.com:80/a/../b/?b=2&a=1&utm_medium=x#f") == "http://example.com/b/?a=1&b=2"
    assert canonicalize_url("https://example.com") == "https://example.com/"
    check_scope("https://news.example.org/x", ["example.org"], [], [])
    for url, allowed, excluded, ex_urls in (("https://evil.com/", ["example.org"], [], []),
                                            ("https://ads.example.org/", [], ["ads.example.org"], []),
                                            ("https://example.org/a?utm_source=z", [], [], ["https://example.org/a"])):
        with pytest.raises(ResearchError) as e:
            check_scope(url, allowed, excluded, ex_urls)
        assert e.value.code is ResearchErrorCode.NETWORK_DENIED
