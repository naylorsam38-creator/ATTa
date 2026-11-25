"""Idempotency: a repeated request with the same Idempotency-Key and the same payload returns the stored result;
the same key with a different payload is a conflict. Keys are scoped per principal and expire."""
from __future__ import annotations

import hashlib
import json
import threading
import time
from dataclasses import dataclass
from typing import Any, Protocol


class IdempotencyConflict(Exception):
    pass


def request_fingerprint(method: str, path: str, body: bytes | Any) -> str:
    if not isinstance(body, (bytes, bytearray)):
        body = json.dumps(body, sort_keys=True, separators=(",", ":"), default=str).encode()
    return hashlib.sha256(method.upper().encode() + b"\0" + path.encode() + b"\0" + bytes(body)).hexdigest()


@dataclass
class StoredResult:
    fingerprint: str
    status: int
    body: Any
    expires_at: float


class IdempotencyStore(Protocol):
    def lookup(self, scope: str, key: str, fingerprint: str) -> StoredResult | None: ...
    def store(self, scope: str, key: str, fingerprint: str, status: int, body: Any) -> None: ...


class InMemoryIdempotencyStore:
    def __init__(self, ttl_seconds: float = 86400.0, clock=time.monotonic):
        self._ttl, self._clock = ttl_seconds, clock
        self._data: dict[tuple[str, str], StoredResult] = {}
        self._lock = threading.Lock()

    def lookup(self, scope: str, key: str, fingerprint: str) -> StoredResult | None:
        with self._lock:
            hit = self._data.get((scope, key))
            if hit is None:
                return None
            if hit.expires_at < self._clock():
                del self._data[(scope, key)]
                return None
            if hit.fingerprint != fingerprint:
                raise IdempotencyConflict("Idempotency-Key reused with a different request")
            return hit

    def store(self, scope: str, key: str, fingerprint: str, status: int, body: Any) -> None:
        with self._lock:
            self._data[(scope, key)] = StoredResult(fingerprint, status, body, self._clock() + self._ttl)


class RedisIdempotencyStore:
    """Same contract, backed by Redis (shared across API replicas)."""

    def __init__(self, client, ttl_seconds: int = 86400, namespace: str = "atta:idem"):
        self._r, self._ttl, self._ns = client, ttl_seconds, namespace

    def lookup(self, scope: str, key: str, fingerprint: str) -> StoredResult | None:
        raw = self._r.get(f"{self._ns}:{scope}:{key}")
        if raw is None:
            return None
        data = json.loads(raw)
        if data["fingerprint"] != fingerprint:
            raise IdempotencyConflict("Idempotency-Key reused with a different request")
        return StoredResult(data["fingerprint"], data["status"], data["body"], 0)

    def store(self, scope: str, key: str, fingerprint: str, status: int, body: Any) -> None:
        payload = json.dumps({"fingerprint": fingerprint, "status": status, "body": body}, default=str)
        self._r.set(f"{self._ns}:{scope}:{key}", payload, ex=self._ttl, nx=True)
