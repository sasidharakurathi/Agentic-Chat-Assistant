"""Keyset ("cursor") pagination for every list endpoint (plan §8).

Why keyset, not `LIMIT … OFFSET`: offset counts rows, and rows keep arriving.
A new message landing between page 1 and page 2 shifts everything down by
one, so the reader sees a row twice; a deletion makes one silently vanish.
Keyset pagination remembers the *sort key of the last row seen* and asks for
rows strictly after it — `(created_at, id) < (:t, :id)` — which is stable
under concurrent inserts and costs an index seek instead of scanning and
discarding `offset` rows.

The sort key must be a **total** order, so every key ends in a unique column
(the primary key). `created_at` alone ties constantly — SQLite's
`CURRENT_TIMESTAMP` has one-second resolution — and a tie at a page boundary
drops or repeats rows.

The cursor is opaque to clients (base64 of the last row's key values). It is
not a capability: the query that consumes it still carries the caller's
tenancy filter, so a forged cursor can at worst skip rows the caller could
already see.
"""

from __future__ import annotations

import base64
import binascii
import json
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Generic, TypeVar

from sqlalchemy import Select, func, literal, tuple_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import InstrumentedAttribute

from app.api.errors import BadRequest
from app.db.types import TZDateTime

T = TypeVar("T")

DEFAULT_LIMIT = 50
MAX_LIMIT = 200


class InvalidCursor(BadRequest):
    code = "invalid_cursor"


def _encode_value(v: Any) -> list[Any]:
    if isinstance(v, datetime):
        return ["dt", v.isoformat()]
    if isinstance(v, uuid.UUID):
        return ["u", str(v)]
    if isinstance(v, int | str) or v is None:
        return ["v", v]
    raise TypeError(f"cannot page on a {type(v).__name__} key")


def _decode_value(tagged: Any) -> Any:
    tag, raw = tagged
    if tag == "dt":
        return datetime.fromisoformat(raw)
    if tag == "u":
        return uuid.UUID(raw)
    if tag == "v":
        return raw
    raise ValueError(tag)


def encode_cursor(values: Sequence[Any]) -> str:
    raw = json.dumps([_encode_value(v) for v in values], separators=(",", ":"))
    return base64.urlsafe_b64encode(raw.encode()).decode().rstrip("=")


def decode_cursor(cursor: str, expected: int) -> list[Any]:
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        values = [_decode_value(t) for t in json.loads(base64.urlsafe_b64decode(padded))]
    except (binascii.Error, ValueError, TypeError, KeyError) as exc:
        raise InvalidCursor("This page cursor is not valid.") from exc
    if len(values) != expected:
        raise InvalidCursor("This page cursor belongs to a different list.")
    return values


def _comparable(expr: Any, sqlite: bool) -> Any:
    """The expression to sort and compare on.

    SQLite has no timestamp type: it stores text, in whatever format wrote
    it. `CURRENT_TIMESTAMP` (every server default) writes
    `2026-09-23 11:43:42`, a bound Python datetime writes
    `2026-09-23 11:43:42.000000` — and compared as text the first sorts
    *before* the second although they are the same instant. The row at a
    page boundary then compares as "after itself" and comes back on every
    page, forever. `julianday()` parses both to the same number. It must be
    used in ORDER BY *and* WHERE: the two have to agree on the order.
    Postgres compares real timestamps and needs none of this.
    """
    if sqlite and isinstance(getattr(expr, "type", None), TZDateTime):
        return func.julianday(expr)
    return expr


@dataclass
class PageResult(Generic[T]):
    items: list[T]
    next_cursor: str | None


async def keyset_page(
    session: AsyncSession,
    stmt: Select[Any],
    keys: Sequence[InstrumentedAttribute[Any]],
    *,
    limit: int = DEFAULT_LIMIT,
    cursor: str | None = None,
    descending: bool = True,
    entities: int = 1,
) -> PageResult[Any]:
    """Run `stmt` one page at a time, ordered by `keys`.

    `stmt` selects `entities` things (one model, or e.g. `(User, Membership)`)
    and must not have its own ORDER BY. With `entities == 1` the items are the
    model instances themselves; otherwise tuples.
    """
    limit = max(1, min(limit, MAX_LIMIT))
    sqlite = session.get_bind().dialect.name == "sqlite"
    sort = [_comparable(k, sqlite) for k in keys]
    if cursor is not None:
        values = decode_cursor(cursor, len(keys))
        after = tuple_(
            *(_comparable(literal(v, k.type), sqlite) for v, k in zip(values, keys, strict=True))
        )
        stmt = stmt.where(tuple_(*sort) < after if descending else tuple_(*sort) > after)
    order = [s.desc() if descending else s.asc() for s in sort]
    # The key values ride along as extra columns, so building the next cursor
    # never depends on how a particular model exposes them.
    stmt = stmt.add_columns(*keys).order_by(*order).limit(limit + 1)

    rows = list((await session.execute(stmt)).all())
    has_more = len(rows) > limit
    rows = rows[:limit]
    items = [r[0] if entities == 1 else tuple(r[:entities]) for r in rows]
    next_cursor = encode_cursor(list(rows[-1][entities:])) if has_more and rows else None
    return PageResult(items=items, next_cursor=next_cursor)


__all__ = [
    "DEFAULT_LIMIT",
    "MAX_LIMIT",
    "InvalidCursor",
    "PageResult",
    "decode_cursor",
    "encode_cursor",
    "keyset_page",
]
