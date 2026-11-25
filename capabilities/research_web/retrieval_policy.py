"""Stage 11 retrieval policy — TRUSTED.

This module is part of the capability package, so it is covered by the package digest and the operator's Ed25519
trust signature: it can change only when the operator re-signs the package. Handlers never take policy from their
arguments (those are worker-controlled). It is loaded from the integrity-verified in-memory snapshot, not re-read
from disk."""

POLICY = {
    "allowed_schemes": ["https", "http"],
    "allowed_ports": [80, 443],
    "max_redirects": 5,
    "allow_https_to_http_redirect": False,
    "max_response_bytes": 5 * 1024 * 1024,
    "max_decompressed_bytes": 10 * 1024 * 1024,
    "connect_timeout_s": 10.0,
    "read_timeout_s": 20.0,
    "total_timeout_s": 30.0,
    "allowed_content_types": [
        "text/html", "application/xhtml+xml", "text/plain", "text/markdown", "text/csv",
        "application/json", "application/ld+json", "application/xml", "text/xml", "application/rss+xml",
        "application/atom+xml", "application/pdf",
    ],
    "blocked_hostname_suffixes": [".localhost", ".local", ".internal", ".lan", ".home", ".corp", ".intranet",
                                  ".arpa", ".onion"],
    "blocked_hostnames": ["localhost", "metadata", "metadata.google.internal", "instance-data",
                          "kubernetes.default", "kubernetes.default.svc"],
    # ONLY a test copy of this package (re-signed with a throw-away test key) may list ports here. The production
    # package ships an empty list, so loopback/private destinations are always refused in production.
    "loopback_ports_allowed_for_tests": [],
    "user_agent": "ATTa-Research/1.0 (controlled retrieval; read-only)",
}
