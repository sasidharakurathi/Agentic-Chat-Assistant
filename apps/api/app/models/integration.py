"""Database integrations (plan §2.4): connections + their cached schema.

The credential itself never lives here — `secret_ref` points at an
envelope-encrypted `secrets` row. Everything on this table is safe to show a
user: host, port, database, username, and what the connection is *allowed* to
do.
"""

from __future__ import annotations

import enum
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import Enum as SAEnum
from sqlalchemy import ForeignKey, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, UUIDPrimaryKeyMixin
from app.db.types import JSONB, TZDateTime


class DbEngine(enum.StrEnum):
    postgres = "postgres"
    mysql = "mysql"
    sqlite = "sqlite"
    mongodb = "mongodb"


class DbConnectionStatus(enum.StrEnum):
    unknown = "unknown"
    ok = "ok"
    error = "error"


#: The default permission profile: read-only, modest caps, nothing destructive.
#: Every field here is enforced by `sql_guard` (3.4) and the adapters (3.2), not
#: merely advisory — see `app/schemas/db_connection.py` for the validated shape.
DEFAULT_PERMISSIONS: dict[str, Any] = {
    "read": True,
    "write": False,
    "ddl": False,
    "allow_tables": [],
    "deny_tables": [],
    "row_limit": 500,
    "statement_timeout_ms": 10_000,
}


class DbConnection(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "db_connections"

    assistant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("assistants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    org_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    engine: Mapped[DbEngine] = mapped_column(
        SAEnum(DbEngine, name="db_engine", native_enum=False, length=20), nullable=False
    )
    host: Mapped[str | None] = mapped_column(String(255))
    port: Mapped[int | None] = mapped_column(Integer)
    #: database name, or the file path for sqlite
    database: Mapped[str] = mapped_column(String(500), nullable=False)
    username: Mapped[str | None] = mapped_column(String(255))
    #: -> secrets.id. Nullable: sqlite has no password, and neither does a
    #: trust-auth Postgres.
    secret_ref: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("secrets.id", ondelete="SET NULL")
    )
    #: -> secrets.id, for a sealed connection string (MongoDB). Kept apart from
    #: `secret_ref` so the summary can say which credential is set without
    #: opening either one.
    uri_secret_ref: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("secrets.id", ondelete="SET NULL")
    )
    #: Stored and returned in plain text — which is exactly why it must never
    #: hold a credential (see `schemas.db_connection.CREDENTIAL_OPTION_KEYS`).
    options: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    ssl: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    permissions: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=lambda: dict(DEFAULT_PERMISSIONS)
    )
    status: Mapped[DbConnectionStatus] = mapped_column(
        SAEnum(DbConnectionStatus, name="db_connection_status", native_enum=False, length=20),
        nullable=False,
        default=DbConnectionStatus.unknown,
    )
    last_checked_at: Mapped[datetime | None] = mapped_column(TZDateTime())
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        TZDateTime(), server_default=func.now(), nullable=False
    )


class DbSchemaCache(UUIDPrimaryKeyMixin, Base):
    """Normalized introspection output, one row per connection.

    Cached because introspecting a large database is slow enough that doing it
    per tool call would dominate a chat turn — and because the agent asks for
    the schema constantly.
    """

    __tablename__ = "db_schema_cache"
    __table_args__ = (UniqueConstraint("db_connection_id", name="uq_db_schema_cache_connection"),)

    db_connection_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("db_connections.id", ondelete="CASCADE"), nullable=False, index=True
    )
    org_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    #: {"schemas": [{"name", "tables": [{"name", "columns": [...], "approx_rows"}]}]}
    #: Denied tables are filtered out *before* caching — never stored, never shown.
    db_schema: Mapped[dict[str, Any]] = mapped_column("schema", JSONB, nullable=False, default=dict)
    refreshed_at: Mapped[datetime] = mapped_column(
        TZDateTime(), server_default=func.now(), nullable=False
    )


__all__ = [
    "DEFAULT_PERMISSIONS",
    "DbConnection",
    "DbConnectionStatus",
    "DbEngine",
    "DbSchemaCache",
]
