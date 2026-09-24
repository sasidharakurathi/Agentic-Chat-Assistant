"""Portable column types.

``TZDateTime`` guarantees timezone-aware UTC ``datetime`` values on the way in
*and* out, regardless of backend. Postgres ``timestamptz`` already does this;
SQLite has no tz-aware storage and would otherwise hand back naive datetimes,
which then blow up when compared with ``datetime.now(UTC)``.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from pgvector.sqlalchemy import Vector as _PgVector
from sqlalchemy import JSON, DateTime, Text
from sqlalchemy.dialects.postgresql import JSONB as _PgJSONB
from sqlalchemy.dialects.postgresql import TSVECTOR as _PgTSVector
from sqlalchemy.engine import Dialect
from sqlalchemy.types import TypeDecorator

# jsonb on Postgres, plain JSON (TEXT) on SQLite so tests still run without PG.
JSONB = JSON().with_variant(_PgJSONB(), "postgresql")

# Real tsvector + GIN on Postgres for full-text search; a plain (unindexed,
# unused) text column on SQLite so the table still exists for offline model
# tests. Hybrid retrieval itself (app/rag/retrieve.py) is Postgres-only —
# there's no meaningful SQLite equivalent to fall back to.
TSV = Text().with_variant(_PgTSVector(), "postgresql")


class Embedding(TypeDecorator[list[float]]):
    """A fixed-dimension embedding vector.

    ``pgvector.sqlalchemy.Vector`` on Postgres (real similarity search via
    HNSW). On SQLite: a JSON-encoded float list in a TEXT column — enough to
    round-trip a value for offline model/CRUD tests, but ``<=>`` distance
    queries only work against Postgres (see the ``integration``-marked tests
    in ``tests/test_rag_vectorstore.py``).
    """

    impl = Text
    cache_ok = True
    # Without this, comparator methods like ``.cosine_distance()`` aren't
    # reachable through the TypeDecorator wrapper — TypeDecorator doesn't
    # proxy the underlying type's comparator by default.
    comparator_factory = _PgVector.comparator_factory

    def __init__(self, dim: int) -> None:
        super().__init__()
        self.dim = dim

    def load_dialect_impl(self, dialect: Dialect) -> Any:
        if dialect.name == "postgresql":
            return dialect.type_descriptor(_PgVector(self.dim))
        return dialect.type_descriptor(Text())

    def process_bind_param(self, value: list[float] | None, dialect: Dialect) -> Any:
        if value is None:
            return None
        if dialect.name == "postgresql":
            return value
        return json.dumps(value)

    def process_result_value(self, value: Any, dialect: Dialect) -> list[float] | None:
        if value is None:
            return None
        if dialect.name == "postgresql":
            return list(value)
        return list(json.loads(value))

    def __repr__(self) -> str:
        return f"Embedding(dim={self.dim})"


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


__all__ = ["JSONB", "TSV", "Embedding", "TZDateTime"]
