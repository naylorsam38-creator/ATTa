"""Stage 11 test configuration. Integration tests use the real PostgreSQL configured for ATTA_ENV=test."""
from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

os.environ.setdefault("ATTA_ENV", "test")
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parents[1]))

from fixture_server import FixtureServer  # noqa: E402


def _db_available() -> bool:
    try:
        from backend.database.session import Database
        from backend.settings import load_settings
        with Database(load_settings("test")).tx() as x:
            return x.execute("SELECT to_regclass('research.runs') IS NOT NULL AS ok").fetchone()["ok"]
    except Exception:  # noqa: BLE001
        return False


DB = _db_available()
requires_db = pytest.mark.skipif(not DB, reason="needs PostgreSQL with migrations applied (ATTA_ENV=test)")


def recent_iso(days: float = 2) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days)).isoformat(timespec="seconds").replace("+00:00", "Z")


@pytest.fixture
def fx():
    with FixtureServer(recent_iso()) as server:
        yield server


@pytest.fixture
def h(tmp_path, fx):
    from harness import build
    return build(tmp_path, port=fx.port)
