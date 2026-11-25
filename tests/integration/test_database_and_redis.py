"""Integration tests against real PostgreSQL (configuration/local.env) and a real redis-server process."""
from __future__ import annotations

import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path

import psycopg
import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from backend.contracts.idempotency import IdempotencyConflict, RedisIdempotencyStore  # noqa: E402
from backend.database.migrate import migrate, status  # noqa: E402
from backend.settings import load_settings  # noqa: E402

pytestmark = pytest.mark.requires_services


def test_migrations_are_idempotent_and_checksummed():
    s = load_settings("test")
    migrate(s)
    assert migrate(s) == [], "second run applies nothing"
    st = status(s)
    assert st and all(x["applied"] and x["checksum_ok"] for x in st)


def test_rls_default_deny_blocks_rows_without_policy():
    s = load_settings("test")
    with psycopg.connect(s.database.conninfo()) as conn:
        assert conn.execute("SELECT extversion FROM pg_extension WHERE extname='vector'").fetchone()
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute("INSERT INTO platform.execution_events (id, type, subject) "
                         "VALUES ('evt_01M88AM98M3S35W4ZXW7AZZV8R', 'x', 'y')")


@pytest.fixture()
def redis_client(tmp_path):
    if not shutil.which("redis-server"):
        pytest.skip("redis-server not installed")
    import redis
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    proc = subprocess.Popen(["redis-server", "--port", str(port), "--bind", "127.0.0.1", "--save", "", "--dir",
                             str(tmp_path)], stdout=subprocess.DEVNULL)
    client = redis.Redis(port=port)
    for _ in range(50):
        try:
            client.ping()
            break
        except redis.ConnectionError:
            time.sleep(0.1)
    yield client
    proc.terminate()
    proc.wait(5)


def test_redis_idempotency_store(redis_client):
    store = RedisIdempotencyStore(redis_client, ttl_seconds=60)
    store.store("u", "k", "fp1", 200, {"a": 1})
    assert store.lookup("u", "k", "fp1").body == {"a": 1}
    with pytest.raises(IdempotencyConflict):
        store.lookup("u", "k", "fp2")
    assert 0 < redis_client.ttl("atta:idem:u:k") <= 60
