"""Cursor pagination. Cursors are opaque base64 offsets; limits are bounded."""
from __future__ import annotations

import base64
from typing import Generic, Sequence, TypeVar

from pydantic import BaseModel, Field

T = TypeVar("T")
MAX_LIMIT = 200


class PageRequest(BaseModel):
    limit: int = Field(default=50, ge=1, le=MAX_LIMIT)
    cursor: str | None = None


class Page(BaseModel, Generic[T]):
    items: list[T]
    next_cursor: str | None = None
    total: int


def _encode(offset: int) -> str:
    return base64.urlsafe_b64encode(f"o:{offset}".encode()).decode().rstrip("=")


def _decode(cursor: str) -> int:
    try:
        raw = base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4)).decode()
        if not raw.startswith("o:"):
            raise ValueError
        offset = int(raw[2:])
        if offset < 0:
            raise ValueError
        return offset
    except Exception as exc:  # noqa: BLE001
        raise ValueError("invalid pagination cursor") from exc


def paginate(items: Sequence[T], request: PageRequest) -> Page[T]:
    start = _decode(request.cursor) if request.cursor else 0
    chunk = list(items[start:start + request.limit])
    end = start + len(chunk)
    return Page[T](items=chunk, next_cursor=_encode(end) if end < len(items) else None, total=len(items))
