"""Ordered, checksummed SQL migrations + RLS policies. Applied migrations are immutable."""
from __future__ import annotations

import hashlib
from pathlib import Path

import psycopg

from backend.settings import REPO_ROOT, Settings

MIGRATIONS = REPO_ROOT / "database" / "migrations"
POLICIES = REPO_ROOT / "database" / "policies"


class MigrationError(RuntimeError):
    pass


def _files(d: Path) -> list[Path]:
    return sorted(p for p in d.glob("*.sql") if p.name[:4].isdigit())


def _checksum(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def status(settings: Settings) -> list[dict]:
    with psycopg.connect(settings.database.conninfo()) as conn:
        applied = _applied(conn)
    out = []
    for kind, d in (("migration", MIGRATIONS), ("policy", POLICIES)):
        for f in _files(d):
            key = f"{kind}:{f.name}"
            rec = applied.get(key)
            out.append({"name": key, "applied": rec is not None,
                        "checksum_ok": rec is None or rec == _checksum(f)})
    return out


def _applied(conn) -> dict[str, str]:
    conn.execute("CREATE TABLE IF NOT EXISTS public.atta_schema_migrations (name text PRIMARY KEY, "
                 "checksum text NOT NULL, applied_at timestamptz NOT NULL DEFAULT now())")
    return dict(conn.execute("SELECT name, checksum FROM public.atta_schema_migrations").fetchall())


def migrate(settings: Settings) -> list[str]:
    done = []
    with psycopg.connect(settings.database.conninfo(), autocommit=False) as conn:
        conn.execute("SELECT pg_advisory_xact_lock(7210001)")
        applied = _applied(conn)
        for kind, d in (("migration", MIGRATIONS), ("policy", POLICIES)):
            for f in _files(d):
                key, digest = f"{kind}:{f.name}", _checksum(f)
                if key in applied:
                    if applied[key] != digest:
                        raise MigrationError(f"{key} was modified after being applied (checksum mismatch)")
                    continue
                conn.execute(f.read_text(encoding="utf-8"))
                conn.execute("INSERT INTO public.atta_schema_migrations (name, checksum) VALUES (%s, %s)", (key, digest))
                done.append(key)
        conn.commit()
    return done
