"""Turning driver values into something safe to put in a model's context
(plan §6.3, "Normalize results").

Every adapter runs its rows through this, for two reasons:

1. **JSON-safety.** `Decimal`, `datetime`, `UUID`, `bytes` and friends are not
   JSON-serialisable, and a tool result that can't be serialised fails the
   turn *after* the query has already run.
2. **Size.** A single `bytea` column or a wide JSON blob can be megabytes. It
   would be silently truncated by the model's context window rather than by
   us, which is the same outcome with none of the honesty — so caps are
   applied here and reported via `truncated`.

`Decimal` deliberately becomes a **string**, not a float: money is the most
common decimal column there is, and `float("0.1") + float("0.2")` is exactly
the wrong answer to show someone asking about a balance.
"""

from __future__ import annotations

import base64
import datetime as dt
import decimal
import ipaddress
import uuid
from typing import Any

#: Beyond this, a single cell is replaced by a short note. Large enough for
#: real text columns, small enough that one row can't blow the context.
MAX_CELL_CHARS = 4_000

#: Total serialized budget for a whole result set.
MAX_TOTAL_CHARS = 100_000

#: Binary bigger than this is summarised rather than base64'd into context.
MAX_INLINE_BYTES = 256


def normalize_value(value: Any) -> Any:  # noqa: PLR0911 - a type dispatcher
    if value is None or isinstance(value, bool | int | str):
        return value
    if isinstance(value, float):
        return value
    if isinstance(value, decimal.Decimal):
        # str, not float — see the module docstring.
        return str(value)
    if isinstance(value, dt.datetime | dt.date | dt.time):
        return value.isoformat()
    if isinstance(value, dt.timedelta):
        return str(value)
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, bytes | bytearray | memoryview):
        raw = bytes(value)
        if len(raw) > MAX_INLINE_BYTES:
            return f"<{len(raw)} bytes>"
        return f"base64:{base64.b64encode(raw).decode('ascii')}"
    if isinstance(value, ipaddress.IPv4Address | ipaddress.IPv6Address | ipaddress.IPv4Network):
        return str(value)
    if isinstance(value, list | tuple):
        return [normalize_value(v) for v in value]
    if isinstance(value, dict):
        return {str(k): normalize_value(v) for k, v in value.items()}
    if isinstance(value, set | frozenset):
        return sorted(normalize_value(v) for v in value)
    # Unknown driver type (a PG range, a custom enum, a Mongo ObjectId...).
    # str() is the honest fallback; the alternative is failing the turn.
    return str(value)


def _clip(value: Any) -> Any:
    if isinstance(value, str) and len(value) > MAX_CELL_CHARS:
        return value[:MAX_CELL_CHARS] + f"… <truncated, {len(value)} chars>"
    return value


def normalize_rows(
    raw_rows: list[Any],
    columns: list[str],
    *,
    max_rows: int,
    max_total_chars: int = MAX_TOTAL_CHARS,
) -> tuple[list[list[Any]], bool]:
    """Normalize, clip and cap. Returns `(rows, truncated)`.

    Row cap and byte budget are both enforced: `LIMIT 500` is no protection
    when the 500 rows each hold a 2 MB document.
    """
    rows: list[list[Any]] = []
    truncated = False
    budget = max_total_chars

    for raw in raw_rows:
        if len(rows) >= max_rows:
            truncated = True
            break
        if isinstance(raw, dict):
            values = [raw.get(c) for c in columns]
        elif isinstance(raw, list | tuple):
            values = list(raw)
        else:  # a driver Record that supports neither
            values = [raw]

        row = [_clip(normalize_value(v)) for v in values]
        cost = sum(len(str(v)) for v in row)
        if cost > budget:
            truncated = True
            break
        budget -= cost
        rows.append(row)

    return rows, truncated


__all__ = [
    "MAX_CELL_CHARS",
    "MAX_INLINE_BYTES",
    "MAX_TOTAL_CHARS",
    "normalize_rows",
    "normalize_value",
]
