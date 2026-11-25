"""Runtime database access with per-transaction tenant/user context for row-level security."""
from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator

import psycopg
from psycopg.rows import dict_row

from backend.settings import Settings


class Database:
    def __init__(self, settings: Settings):
        self.settings = settings

    @contextmanager
    def tx(self, *, tenant_id: str | None = None, user_id: str | None = None) -> Iterator[psycopg.Connection]:
        """One transaction as the runtime role. RLS context is transaction-local (set_config(..., true))."""
        with psycopg.connect(self.settings.database.conninfo(), row_factory=dict_row) as conn:
            with conn.transaction():
                if tenant_id:
                    conn.execute("SELECT set_config('atta.tenant_id', %s, true)", (tenant_id,))
                if user_id:
                    conn.execute("SELECT set_config('atta.user_id', %s, true)", (user_id,))
                yield conn
