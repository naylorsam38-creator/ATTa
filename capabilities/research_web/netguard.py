"""Controlled source retrieval (Stage 11 §10) — standard library only; runs inside the isolated handler process.

Every hop of every request is validated BEFORE a socket is opened:
  scheme allow-list -> no credentials in URL -> hostname syntax / blocked names -> port allow-list ->
  DNS resolution -> EVERY resolved address must be globally routable (no loopback, private, link-local, CGNAT,
  multicast, reserved, unspecified, IPv4-mapped/compatible private, metadata endpoints) -> connect to the
  validated address itself (pinned; a second DNS answer cannot redirect the connection: no DNS rebinding).
Redirects are followed manually, each target re-validated; https->http downgrades and cross-host redirects are
refused (a cross-host target is returned so the controller can plan a separately authorised fetch).
Responses are bounded by Content-Length, a streamed byte cap, a decompression cap, per-operation socket timeouts
and a total deadline. Only allow-listed content types are accepted.

Errors are returned as Stage 11 error codes, never raised as generic failures."""
from __future__ import annotations

import http.client
import ipaddress
import re
import socket
import ssl
import time
import zlib
from urllib.parse import urljoin, urlsplit, urlunsplit

# Stage 11 error codes (mirrors backend/research/errors.py — kept identical by tests/research/test_netguard.py)
NETWORK_DENIED = "NETWORK_DENIED"
SOURCE_UNAVAILABLE = "SOURCE_UNAVAILABLE"
SOURCE_TOO_LARGE = "SOURCE_TOO_LARGE"
UNSUPPORTED_CONTENT = "UNSUPPORTED_CONTENT"

_HOST_LABEL = re.compile(r"^(?!-)[a-z0-9-]{1,63}(?<!-)$")
_NUMERIC_HOST = re.compile(r"^(0x[0-9a-f]+|[0-9]+)(\.(0x[0-9a-f]+|[0-9]+))*$")
# Well-known cloud metadata addresses. All are also non-global and refused by classify_ip; listed explicitly so the
# refusal reason names them.
METADATA_ADDRESSES = frozenset({"169.254.169.254", "169.254.170.2", "100.100.100.200", "192.0.0.192",
                                "fd00:ec2::254"})


class RetrievalError(Exception):
    def __init__(self, code: str, detail: str, **extra):
        super().__init__(f"{code}: {detail}")
        self.code, self.detail, self.extra = code, detail, extra


def classify_ip(value: str) -> str | None:
    """None when the address is a public, globally routable unicast address; otherwise the reason it is refused."""
    try:
        ip = ipaddress.ip_address(value.split("%", 1)[0])
    except ValueError:
        return "not an IP address"
    if str(ip) in METADATA_ADDRESSES:
        return "cloud metadata endpoint"
    if ip.version == 6:
        mapped = ip.ipv4_mapped or (ip.sixtofour if ip.sixtofour else None)
        if mapped is not None:
            inner = classify_ip(str(mapped))
            if inner:
                return f"IPv6-embedded IPv4 address: {inner}"
        if ip.teredo:
            return "Teredo tunnelled address"
        if ip in ipaddress.ip_network("64:ff9b::/96"):     # NAT64: embedded IPv4 decides
            inner = classify_ip(str(ipaddress.IPv4Address(int(ip) & 0xFFFFFFFF)))
            if inner:
                return f"NAT64-embedded IPv4 address: {inner}"
    for attr, why in (("is_loopback", "loopback address"), ("is_unspecified", "unspecified address"),
                      ("is_link_local", "link-local address"), ("is_multicast", "multicast address"),
                      ("is_private", "private network address"), ("is_reserved", "reserved address")):
        if getattr(ip, attr):
            return why
    if ip.version == 4 and ip in ipaddress.ip_network("100.64.0.0/10"):
        return "carrier-grade NAT (shared) address"
    if not ip.is_global:
        return "not a globally routable address"
    return None


def normalise_host(host: str) -> str:
    try:
        return host.encode("idna").decode("ascii").lower().rstrip(".")
    except UnicodeError as exc:
        raise RetrievalError(NETWORK_DENIED, f"hostname is not valid IDNA: {exc}") from exc


def validate_url(url: str, policy: dict) -> tuple:
    """Structural validation of one URL. Returns (scheme, host, port, path_with_query, normalised_url)."""
    if not isinstance(url, str) or not url or len(url) > 2048:
        raise RetrievalError(NETWORK_DENIED, "URL missing or longer than 2048 characters")
    if any(ord(ch) < 0x21 or ch in "\\" for ch in url):
        raise RetrievalError(NETWORK_DENIED, "URL contains whitespace, control characters or backslashes")
    try:
        parts = urlsplit(url)
        port = parts.port
    except ValueError as exc:
        raise RetrievalError(NETWORK_DENIED, f"malformed URL: {exc}") from exc
    scheme = (parts.scheme or "").lower()
    if scheme not in policy["allowed_schemes"]:
        raise RetrievalError(NETWORK_DENIED, f"scheme '{scheme or '(none)'}' is not permitted",
                             permitted=list(policy["allowed_schemes"]))
    if parts.username is not None or parts.password is not None or "@" in parts.netloc:
        raise RetrievalError(NETWORK_DENIED, "credentials in URLs are not permitted")
    if not parts.hostname:
        raise RetrievalError(NETWORK_DENIED, "URL has no host")
    host = normalise_host(parts.hostname)
    port = port or (443 if scheme == "https" else 80)
    literal = None
    try:
        literal = ipaddress.ip_address(host.strip("[]"))
    except ValueError:
        pass
    test_loopback = port in (policy.get("loopback_ports_allowed_for_tests") or [])
    if literal is not None:
        why = classify_ip(str(literal))
        if why and not (test_loopback and literal.is_loopback):
            raise RetrievalError(NETWORK_DENIED, f"destination {literal} refused: {why}")
    else:
        if _NUMERIC_HOST.match(host):
            raise RetrievalError(NETWORK_DENIED, "numeric/hex host encodings are not permitted")
        if host in policy["blocked_hostnames"] or any(host.endswith(s) for s in policy["blocked_hostname_suffixes"]):
            raise RetrievalError(NETWORK_DENIED, f"hostname '{host}' is an internal or local name")
        if "." not in host:
            raise RetrievalError(NETWORK_DENIED, f"single-label hostname '{host}' is not a public name")
        if not all(_HOST_LABEL.match(lbl) for lbl in host.split(".")):
            raise RetrievalError(NETWORK_DENIED, f"hostname '{host}' is not a valid DNS name")
    if port not in policy["allowed_ports"] and not test_loopback:
        raise RetrievalError(NETWORK_DENIED, f"port {port} is not permitted", permitted=list(policy["allowed_ports"]))
    path = parts.path or "/"
    if parts.query:
        path += "?" + parts.query
    netloc = (f"[{host}]" if literal is not None and literal.version == 6 else host)
    if (scheme, port) not in (("https", 443), ("http", 80)):
        netloc += f":{port}"
    normalised = urlunsplit((scheme, netloc, parts.path or "/", parts.query, ""))
    return scheme, host, port, path, normalised


def resolve_public(host: str, port: int, policy: dict, resolver=socket.getaddrinfo) -> str:
    """Resolve and require EVERY answer to be public. Returns the address to connect to (pinned)."""
    try:
        literal = ipaddress.ip_address(host.strip("[]"))
        addrs = [str(literal)]
    except ValueError:
        try:
            infos = resolver(host, port, 0, socket.SOCK_STREAM)
        except (socket.gaierror, UnicodeError, OSError) as exc:
            raise RetrievalError(SOURCE_UNAVAILABLE, f"DNS resolution failed for {host}: {exc}") from exc
        addrs = list(dict.fromkeys(i[4][0] for i in infos))
    if not addrs:
        raise RetrievalError(SOURCE_UNAVAILABLE, f"{host} did not resolve")
    test_loopback = port in (policy.get("loopback_ports_allowed_for_tests") or [])
    for a in addrs:
        why = classify_ip(a)
        if why and not (test_loopback and ipaddress.ip_address(a.split("%")[0]).is_loopback):
            raise RetrievalError(NETWORK_DENIED, f"{host} resolves to {a}: {why}", resolved=addrs)
    return addrs[0]


class _PinnedHTTP(http.client.HTTPConnection):
    def __init__(self, host, port, ip, timeout):
        super().__init__(host, port, timeout=timeout)
        self._ip = ip

    def connect(self):
        self.sock = socket.create_connection((self._ip, self.port), self.timeout)


class _PinnedHTTPS(http.client.HTTPSConnection):
    def __init__(self, host, port, ip, timeout, context):
        super().__init__(host, port, timeout=timeout, context=context)
        self._ip, self._ctx = ip, context

    def connect(self):
        raw = socket.create_connection((self._ip, self.port), self.timeout)
        self.sock = self._ctx.wrap_socket(raw, server_hostname=self.host)


def _content_type(header: str | None) -> tuple[str, str | None]:
    if not header:
        return "", None
    main, _, rest = header.partition(";")
    charset = None
    for p in rest.split(";"):
        k, _, v = p.strip().partition("=")
        if k.lower() == "charset" and v:
            charset = v.strip().strip('"').lower()
    return main.strip().lower(), charset


def _same_site(a: str, b: str) -> bool:
    strip = lambda h: h[4:] if h.startswith("www.") else h  # noqa: E731
    return strip(a) == strip(b)


def fetch(url: str, policy: dict, *, accept: str = "*/*", resolver=socket.getaddrinfo,
          ssl_context: ssl.SSLContext | None = None, clock=time.monotonic) -> dict:
    """Retrieve one URL under the policy. Returns a dict with body bytes; raises RetrievalError with a code."""
    deadline = clock() + float(policy["total_timeout_s"])
    redirects: list[dict] = []
    current = url
    first_host = None
    ctx = ssl_context or ssl.create_default_context()
    for hop in range(int(policy["max_redirects"]) + 1):
        scheme, host, port, path, normalised = validate_url(current, policy)
        if first_host is None:
            first_host = host
        ip = resolve_public(host, port, policy, resolver)
        remaining = deadline - clock()
        if remaining <= 0:
            raise RetrievalError(SOURCE_UNAVAILABLE, "total retrieval deadline exceeded", timeout=True)
        timeout = min(float(policy["read_timeout_s"]), float(policy["connect_timeout_s"]), remaining)
        conn = (_PinnedHTTPS(host, port, ip, timeout, ctx) if scheme == "https" else _PinnedHTTP(host, port, ip, timeout))
        try:
            conn.request("GET", path, headers={"User-Agent": policy["user_agent"], "Accept": accept,
                                               "Accept-Encoding": "gzip, deflate, identity", "Connection": "close"})
            resp = conn.getresponse()
            status = resp.status
            headers = {k.lower(): v for k, v in resp.getheaders()}
            if status in (301, 302, 303, 307, 308):
                loc = headers.get("location")
                conn.close()
                if not loc:
                    raise RetrievalError(SOURCE_UNAVAILABLE, f"HTTP {status} redirect without Location")
                target = urljoin(normalised, loc)
                t_scheme = urlsplit(target).scheme.lower()
                redirects.append({"status": status, "from": normalised, "to": target})
                if t_scheme not in policy["allowed_schemes"]:
                    raise RetrievalError(NETWORK_DENIED, f"redirect to scheme '{t_scheme}' is not permitted",
                                         redirects=redirects)
                if scheme == "https" and t_scheme == "http" and not policy["allow_https_to_http_redirect"]:
                    raise RetrievalError(NETWORK_DENIED, "redirect downgrades https to http", redirects=redirects)
                t_host = normalise_host(urlsplit(target).hostname or "")
                if not _same_site(t_host, first_host):
                    raise RetrievalError(NETWORK_DENIED, f"cross-host redirect to {t_host} needs its own authorised "
                                         "fetch", redirects=redirects, redirect_target=target)
                current = target
                continue
            if status >= 400:
                conn.close()
                raise RetrievalError(SOURCE_UNAVAILABLE, f"HTTP {status}", http_status=status, redirects=redirects)
            ctype, charset = _content_type(headers.get("content-type"))
            if ctype not in policy["allowed_content_types"]:
                conn.close()
                raise RetrievalError(UNSUPPORTED_CONTENT, f"content type '{ctype or '(none)'}' is not accepted",
                                     content_type=ctype, redirects=redirects)
            limit = int(policy["max_response_bytes"])
            declared = headers.get("content-length")
            if declared and declared.isdigit() and int(declared) > limit:
                conn.close()
                raise RetrievalError(SOURCE_TOO_LARGE, f"declared size {declared} exceeds {limit} bytes",
                                     redirects=redirects)
            chunks, total = [], 0
            while True:
                if clock() > deadline:
                    conn.close()
                    raise RetrievalError(SOURCE_UNAVAILABLE, "total retrieval deadline exceeded", timeout=True,
                                         redirects=redirects)
                block = resp.read(65536)
                if not block:
                    break
                total += len(block)
                if total > limit:
                    conn.close()
                    raise RetrievalError(SOURCE_TOO_LARGE, f"response exceeds {limit} bytes", redirects=redirects)
                chunks.append(block)
            conn.close()
            body = b"".join(chunks)
            encoding = (headers.get("content-encoding") or "identity").lower().strip()
            if encoding in ("gzip", "x-gzip", "deflate"):
                body = _bounded_decompress(body, encoding, int(policy["max_decompressed_bytes"]))
            elif encoding not in ("identity", ""):
                raise RetrievalError(UNSUPPORTED_CONTENT, f"content encoding '{encoding}' is not supported")
            return {"requested_url": url, "final_url": normalised, "status": status, "content_type": ctype,
                    "charset": charset, "body": body, "redirects": redirects, "resolved_ip": ip,
                    "headers": {k: headers[k] for k in ("date", "last-modified", "etag", "content-language")
                                if k in headers}}
        except RetrievalError:
            raise
        except (socket.timeout, TimeoutError) as exc:
            raise RetrievalError(SOURCE_UNAVAILABLE, f"timed out: {exc}", timeout=True, redirects=redirects) from exc
        except ssl.SSLError as exc:
            raise RetrievalError(SOURCE_UNAVAILABLE, f"TLS failure: {exc}", redirects=redirects) from exc
        except (OSError, http.client.HTTPException) as exc:
            raise RetrievalError(SOURCE_UNAVAILABLE, f"{type(exc).__name__}: {exc}", redirects=redirects) from exc
        finally:
            conn.close()
    raise RetrievalError(NETWORK_DENIED, f"more than {policy['max_redirects']} redirects", redirects=redirects)


def _bounded_decompress(body: bytes, encoding: str, limit: int) -> bytes:
    wbits = 16 + zlib.MAX_WBITS if "gzip" in encoding else zlib.MAX_WBITS
    try:
        d = zlib.decompressobj(wbits)
        out = d.decompress(body, limit + 1)
        if len(out) > limit or d.unconsumed_tail:
            raise RetrievalError(SOURCE_TOO_LARGE, f"decompressed content exceeds {limit} bytes")
        return out
    except zlib.error as exc:
        if encoding == "deflate":       # some servers send raw deflate
            try:
                d = zlib.decompressobj(-zlib.MAX_WBITS)
                out = d.decompress(body, limit + 1)
                if len(out) > limit or d.unconsumed_tail:
                    raise RetrievalError(SOURCE_TOO_LARGE, f"decompressed content exceeds {limit} bytes") from exc
                return out
            except zlib.error:
                pass
        raise RetrievalError(UNSUPPORTED_CONTENT, f"corrupt {encoding} content: {exc}") from exc
