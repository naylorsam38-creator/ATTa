"""Real dependency probes. Each returns DependencyHealth from an actual connection attempt — nothing is assumed."""
from __future__ import annotations

import socket
import time
from typing import Callable

import httpx

from backend.contracts import DependencyHealth, HealthStatus
from backend.settings import HealthProbeSpec, Settings


def _timed(name: str, essential: bool, fn: Callable[[], str | None]) -> DependencyHealth:
    t0 = time.perf_counter()
    try:
        detail = fn()
        status = HealthStatus.HEALTHY
    except Exception as exc:  # noqa: BLE001 — any failure is an unhealthy dependency, reported with its cause
        detail, status = f"{type(exc).__name__}: {exc}"[:400], HealthStatus.UNHEALTHY
    return DependencyHealth(name=name, essential=essential, status=status,
                            latency_ms=round((time.perf_counter() - t0) * 1000, 2), detail=detail)


def probe_tcp(host: str, port: int, timeout: float) -> str:
    with socket.create_connection((host, port), timeout=timeout):
        return f"tcp {host}:{port} accepting connections"


def probe_http(url: str, timeout: float) -> str:
    r = httpx.get(url, timeout=timeout, trust_env=False)
    if r.status_code >= 400:
        raise RuntimeError(f"HTTP {r.status_code} from {url}")
    return f"HTTP {r.status_code} from {url}"


def probe_postgres(settings: Settings, timeout: float) -> str:
    import psycopg
    db = settings.database
    with psycopg.connect(db.conninfo(), connect_timeout=max(1, int(timeout))) as conn:
        version = conn.execute("SELECT current_setting('server_version')").fetchone()[0]
        if db.require_pgvector:
            row = conn.execute("SELECT installed_version, default_version FROM pg_available_extensions "
                               "WHERE name = 'vector'").fetchone()
            if row is None:
                raise RuntimeError(f"PostgreSQL {version} reachable but the pgvector extension is not available")
            if row[0] is None:
                raise RuntimeError(f"pgvector {row[1]} available but not installed in database '{db.name}' "
                                   "(apply database/migrations)")
            return f"PostgreSQL {version}, pgvector {row[0]}"
        return f"PostgreSQL {version}"


def probe_redis(settings: Settings, timeout: float) -> str:
    import redis
    from redis.backoff import NoBackoff
    from redis.retry import Retry
    r = settings.redis
    client = redis.Redis(host=r.host, port=r.port, db=r.db,
                         password=r.password.get_secret_value() if r.password else None,
                         socket_timeout=timeout, socket_connect_timeout=timeout,
                         retry=Retry(NoBackoff(), 0))  # a probe reports the truth now; no hidden retries
    try:
        if client.ping() is not True:
            raise RuntimeError("PING did not return PONG")
        return f"Redis {client.info('server').get('redis_version')} at {r.host}:{r.port}"
    finally:
        client.close()


def probe_s3(settings: Settings, timeout: float) -> str:
    import boto3
    from botocore.config import Config
    s = settings.object_storage
    if s.access_key_id is None or s.secret_access_key is None:
        raise RuntimeError("object storage credentials not configured (ATTA_S3_ACCESS_KEY_ID / ATTA_S3_SECRET_ACCESS_KEY)")
    client = boto3.client("s3", endpoint_url=s.endpoint_url, region_name=s.region,
                          aws_access_key_id=s.access_key_id.get_secret_value(),
                          aws_secret_access_key=s.secret_access_key.get_secret_value(),
                          config=Config(connect_timeout=timeout, read_timeout=timeout, retries={"max_attempts": 1}))
    client.head_bucket(Bucket=s.bucket)
    return f"bucket '{s.bucket}' reachable at {s.endpoint_url or 'AWS'}"


def render(value: str, settings: Settings, extra: dict[str, str] | None = None) -> str:
    table = {"api.host": settings.api.host, "api.port": str(settings.api.port), "redis.host": settings.redis.host,
             "redis.port": str(settings.redis.port), **(extra or {})}
    for k, v in table.items():
        value = value.replace("{" + k + "}", v)
    return value


def probe_service(name: str, essential: bool, spec: HealthProbeSpec, settings: Settings) -> DependencyHealth:
    t = spec.timeout_s
    if spec.type == "none":
        return DependencyHealth(name=name, essential=essential, status=HealthStatus.UNKNOWN, detail="no probe")
    fns: dict[str, Callable[[], str]] = {
        "tcp": lambda: probe_tcp(render(spec.host or "127.0.0.1", settings), int(spec.port or 0), t),
        "http": lambda: probe_http(render(spec.url or "", settings), t),
        "postgres": lambda: probe_postgres(settings, t),
        "redis": lambda: probe_redis(settings, t),
        "s3": lambda: probe_s3(settings, t),
    }
    return _timed(name, essential, fns[spec.type])


def probe_model_endpoint(name: str, base_url: str, health_path: str | None, timeout: float = 2.0) -> DependencyHealth:
    url = base_url.rstrip("/") + (health_path or "/")
    return _timed(f"model:{name}", False, lambda: probe_http(url, timeout))
