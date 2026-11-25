"""Ordered, checksummed SQL migrations + RLS policies. Applied migrations are immutable.

Two credentials (DECISIONS D-024): migrate() uses the migration role; status() and every runtime check use the
runtime role over a READ ONLY session, so the runtime can observe migration state but never change it."""
from __future__ import annotations

import hashlib
from pathlib import Path

import psycopg

from backend.settings import REPO_ROOT, Settings

MIGRATIONS = REPO_ROOT / "database" / "migrations"
POLICIES = REPO_ROOT / "database" / "policies"
LEDGER = "public.atta_schema_migrations"


class MigrationError(RuntimeError):
    pass


def _files(d: Path) -> list[Path]:
    return sorted(p for p in d.glob("*.sql") if p.name[:4].isdigit())


def _checksum(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def expected() -> list[tuple[str, str]]:
    return [(f"{kind}:{f.name}", _checksum(f)) for kind, d in (("migration", MIGRATIONS), ("policy", POLICIES))
            for f in _files(d)]


def status(settings: Settings) -> list[dict]:
    """Read-only, runtime credentials. Raises if the ledger is unreadable."""
    with psycopg.connect(settings.database.conninfo(read_only=True)) as conn:
        exists = conn.execute("SELECT to_regclass(%s)", (LEDGER,)).fetchone()[0]
        applied = dict(conn.execute(f"SELECT name, checksum FROM {LEDGER}").fetchall()) if exists else {}
    return [{"name": name, "applied": name in applied, "checksum_ok": name not in applied or applied[name] == digest}
            for name, digest in expected()]


def migrate(settings: Settings) -> list[str]:
    if settings.database.migration_password is None and settings.database.migration_user != settings.database.user:
        raise MigrationError("migration credentials missing: set ATTA_DATABASE_MIGRATION_PASSWORD "
                             "(the runtime role is not allowed to change the schema)")
    done = []
    with psycopg.connect(settings.database.conninfo(migration=True), autocommit=False) as conn:
        conn.execute("SELECT pg_advisory_xact_lock(7210001)")
        conn.execute(f"CREATE TABLE IF NOT EXISTS {LEDGER} (name text PRIMARY KEY, checksum text NOT NULL, "
                     "applied_at timestamptz NOT NULL DEFAULT now())")
        applied = dict(conn.execute(f"SELECT name, checksum FROM {LEDGER}").fetchall())
        files = {f"{k}:{f.name}": f for k, d in (("migration", MIGRATIONS), ("policy", POLICIES)) for f in _files(d)}
        # Migrations first, then policies, each in file order (policies may reference later migrations' tables).
        for key, digest in expected():
            if key in applied:
                if applied[key] != digest:
                    raise MigrationError(f"{key} was modified after being applied (checksum mismatch)")
                continue
            conn.execute(files[key].read_text(encoding="utf-8"))
            conn.execute(f"INSERT INTO {LEDGER} (name, checksum) VALUES (%s, %s)", (key, digest))
            done.append(key)
        conn.commit()
    return done
