"""Request/response shapes for database connections (tasks 3.2 / 3.7).

The rule that shapes this whole module: **a credential goes in and never comes
back out.** `DbConnectionCreate` accepts a password or a connection string;
`DbConnectionSummary` has no field that could carry either. That is enforced
by the types rather than by remembering to strip it at each call site — and
`options`, the one free-form field that *is* returned, refuses to hold one.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

from app.models.integration import DbConnectionStatus, DbEngine
from app.schemas.common import ApiModel, ORMModel
from app.security.redact import is_secret_key, strip_secrets

Engine = Literal["postgres", "mysql", "sqlite", "mongodb"]

#: Keys that would put a credential into `options`, which is stored and
#: returned as plain text. Checked case-insensitively.
CREDENTIAL_OPTION_KEYS = frozenset(
    {"uri", "url", "dsn", "connection_string", "connection_uri", "password", "passwd", "secret"}
)


def _credential_keys(value: Any, path: str = "") -> list[str]:
    """Every key, at any depth, that names a credential or holds something
    shaped like one. It used to look at the top-level keys of `options`
    only: `{"auth": {"password": ...}}` and anything under `ssl` (a client
    key, say) were stored and returned in the clear."""
    found: list[str] = []
    if isinstance(value, dict):
        for key, inner in value.items():
            name = f"{path}.{key}" if path else str(key)
            if str(key).lower() in CREDENTIAL_OPTION_KEYS or is_secret_key(str(key)):
                found.append(name)
            else:
                found += _credential_keys(inner, name)
    elif isinstance(value, list):
        for n, inner in enumerate(value):
            found += _credential_keys(inner, f"{path}[{n}]")
    elif isinstance(value, str) and strip_secrets(value) != value:
        found.append(path)
    return found


def _refuse_credentials_in_options(options: dict[str, Any] | None) -> None:
    """`options` is shown to anyone who can view the connection. Refused rather
    than silently moved, so whoever put a secret there learns it belongs in a
    write-only field — and the error names the key, never the value."""
    bad = sorted(_credential_keys(options or {}))
    if bad:
        raise ValueError(
            f"options may not contain {', '.join(bad)}: options are stored and shown in plain "
            "text. Send a connection string as `connection_uri` and a password as `password`; "
            "both are encrypted and never returned."
        )


class DbPermissions(ApiModel):
    """The permission profile (task 3.7). Enforced by `sql_guard` and the
    adapters — not advisory."""

    read: bool = True
    write: bool = False
    ddl: bool = False
    #: When non-empty, an exhaustive allow-list: nothing else is queryable.
    allow_tables: list[str] = Field(default_factory=list)
    deny_tables: list[str] = Field(default_factory=list)
    row_limit: int = Field(default=500, ge=1, le=10_000)
    statement_timeout_ms: int = Field(default=10_000, ge=100, le=120_000)

    @model_validator(mode="after")
    def _ddl_implies_write(self) -> DbPermissions:
        # Schema changes without write is an incoherent profile: every DDL
        # path (CREATE TABLE AS, ALTER ... SET DEFAULT) can change data.
        if self.ddl and not self.write:
            raise ValueError("ddl requires write")
        return self


class DbConnectionCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    engine: Engine
    database: str = Field(min_length=1, max_length=500)
    host: str | None = Field(default=None, max_length=255)
    port: int | None = Field(default=None, ge=1, le=65535)
    username: str | None = Field(default=None, max_length=255)
    #: Write-only. Stored envelope-encrypted; never returned by any endpoint.
    password: str | None = Field(default=None, max_length=1024)
    #: Write-only, MongoDB only: a full connection string. Sealed like a
    #: password, because it usually contains one.
    connection_uri: str | None = Field(default=None, max_length=2048)
    options: dict[str, Any] = Field(default_factory=dict)
    ssl: dict[str, Any] = Field(default_factory=dict)
    permissions: DbPermissions = Field(default_factory=DbPermissions)

    @model_validator(mode="after")
    def _engine_needs_its_fields(self) -> DbConnectionCreate:
        _refuse_credentials_in_options(self.options)
        _refuse_credentials_in_options(self.ssl)
        if self.connection_uri and self.engine != "mongodb":
            raise ValueError("connection_uri is only for mongodb; use host/port/username/password")
        if self.engine == "sqlite":
            # `database` is a file path; host/port/credentials are meaningless.
            return self
        if self.engine == "mongodb" and self.connection_uri:
            return self  # the host lives inside the connection string
        if not self.host:
            raise ValueError(f"{self.engine} connections need a host")
        return self


class DbConnectionUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    host: str | None = Field(default=None, max_length=255)
    port: int | None = Field(default=None, ge=1, le=65535)
    database: str | None = Field(default=None, min_length=1, max_length=500)
    username: str | None = Field(default=None, max_length=255)
    #: Omit to leave the stored credential alone; send a new one to replace it.
    password: str | None = Field(default=None, max_length=1024)
    #: Same rules as `password`; MongoDB only (checked against the stored
    #: engine in the service, which is where the engine is known).
    connection_uri: str | None = Field(default=None, max_length=2048)
    options: dict[str, Any] | None = None
    ssl: dict[str, Any] | None = None
    permissions: DbPermissions | None = None

    @model_validator(mode="after")
    def _no_credentials_in_options(self) -> DbConnectionUpdate:
        _refuse_credentials_in_options(self.options)
        return self


class DbConnectionSummary(ORMModel):
    """Everything safe to show. Deliberately has no password field at all."""

    id: uuid.UUID
    assistant_id: uuid.UUID
    name: str
    engine: DbEngine
    host: str | None
    port: int | None
    database: str
    username: str | None
    options: dict[str, Any]
    ssl: dict[str, Any]
    #: Was `dict[str, Any]`, which published no shape at all: the web app's
    #: types had to guess it, and did.
    permissions: DbPermissions
    status: DbConnectionStatus
    last_checked_at: datetime | None
    error: str | None
    created_at: datetime
    #: True when a credential is stored — so the UI can say "password set"
    #: without ever handling the value.
    has_password: bool = False
    #: Same, for a sealed connection string.
    has_connection_uri: bool = False


class DbTestResult(ApiModel):
    ok: bool
    error: str | None = None
    elapsed_ms: int = 0


class DbColumnOut(ApiModel):
    name: str
    type: str
    nullable: bool = True
    pk: bool = False
    fk: str | None = None


class DbTableOut(ApiModel):
    name: str
    columns: list[DbColumnOut] = Field(default_factory=list)
    approx_rows: int | None = None


class DbNamespaceOut(ApiModel):
    """One schema (Postgres/MySQL) or database (Mongo), mirroring
    `datasources.base.SchemaNamespace`."""

    name: str
    tables: list[DbTableOut] = Field(default_factory=list)


class DbSchemaOut(ApiModel):
    schemas: list[DbNamespaceOut] = Field(default_factory=list)
    refreshed_at: datetime | None = None
    #: Tables hidden by the permission profile are not merely omitted from the
    #: payload — they were never introspected or cached.
    filtered: bool = False


__all__ = [
    "DbConnectionCreate",
    "DbConnectionSummary",
    "DbConnectionUpdate",
    "DbPermissions",
    "DbSchemaOut",
    "DbTestResult",
    "Engine",
]
