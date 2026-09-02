"""Portable column types.

``TZDateTime`` guarantees timezone-aware UTC ``datetime`` values on the way in
*and* out, regardless of backend. Postgres ``timestamptz`` already does this;
SQLite has no tz-aware storage and would otherwise hand back naive datetimes,
which then blow up when compared with ``datetime.now(UTC)``.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import DateTime
from sqlalchemy.engine import Dialect
from sqlalchemy.types import TypeDecorator


class TZDateTime(TypeDecorator[datetime]):
    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect: Dialect) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            value = value.replace(tzinfo=UTC)
        return value.astimezone(UTC)

    def process_result_value(self, value: datetime | None, dialect: Dialect) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)

    @property
    def python_type(self) -> type[datetime]:
        return datetime

    def __repr__(self) -> str:  # nicer Alembic autogenerate output
        return "TZDateTime()"

    def _compiler_dispatch(self, visitor: Any, **kw: Any) -> str:  # pragma: no cover
        return self.impl._compiler_dispatch(visitor, **kw)


__all__ = ["TZDateTime"]
