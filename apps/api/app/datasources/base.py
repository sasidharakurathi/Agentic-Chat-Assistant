"""The adapter contract every engine implements (plan §6).

Everything above this layer — the SQL guard, the `sql_*` tools, the schema
cache — is written against these four operations and the normalized shapes
below, never against a driver. That is what makes "add MySQL" a new file
rather than a change everywhere.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol


class DbError(RuntimeError):
    """Anything that went wrong talking to a tenant's database.

    Deliberately one type: the message is shown to the agent (and sometimes
    the user), and driver-specific exception hierarchies leak connection
    details and internal paths into places they shouldn't reach.
    """


@dataclass(frozen=True)
class ConnectionInfo:
    """Everything needed to open a connection, with the password already
    decrypted. Constructed per use and never persisted or logged."""

    engine: str
    database: str
    host: str | None = None
    port: int | None = None
    username: str | None = None
    password: str | None = None
    options: dict[str, Any] = field(default_factory=dict)
    ssl: dict[str, Any] = field(default_factory=dict)

    def __repr__(self) -> str:  # pragma: no cover - defensive
        # A ConnectionInfo in a traceback must not print the password.
        return (
            f"ConnectionInfo(engine={self.engine!r}, host={self.host!r}, "
            f"port={self.port!r}, database={self.database!r}, "
            f"username={self.username!r}, password=<redacted>)"
        )


@dataclass
class Column:
    name: str
    type: str
    nullable: bool = True
    pk: bool = False
    fk: str | None = None


@dataclass
class Table:
    name: str
    columns: list[Column] = field(default_factory=list)
    approx_rows: int | None = None


@dataclass
class SchemaNamespace:
    name: str
    tables: list[Table] = field(default_factory=list)


@dataclass
class QueryResult:
    """A normalized result set. `rows` holds only JSON-safe scalars — see
    `normalize.py` — because this goes straight into a model's context."""

    columns: list[str] = field(default_factory=list)
    rows: list[list[Any]] = field(default_factory=list)
    row_count: int = 0
    #: True when rows or total bytes hit a cap. The agent is told, so it can
    #: say "first 500 rows" rather than presenting a slice as the whole answer.
    truncated: bool = False
    elapsed_ms: int = 0
    #: Rendered statement actually executed, after the guard's rewrites.
    statement: str = ""
    #: Rows *changed* by a mutating statement; None for reads. Deliberately
    #: separate from `row_count`, which counts rows *returned* — an UPDATE
    #: without RETURNING returns none at all, so reporting `row_count` for a
    #: write always says "0 rows affected" no matter what it did.
    affected_rows: int | None = None


class DbAdapter(Protocol):
    """What every engine can do, SQL or not."""

    engine: str

    async def test(self, info: ConnectionInfo) -> None:
        """Open a connection and issue a trivial round-trip. Raises `DbError`."""
        ...

    async def introspect(self, info: ConnectionInfo) -> list[SchemaNamespace]:
        """Every schema/table/column the credential can see. Allow/deny
        filtering happens above this layer, so an adapter never has to be
        trusted to enforce policy."""
        ...


class SqlAdapter(DbAdapter, Protocol):
    """Engines that take a SQL string.

    Split out rather than bolted onto `DbAdapter` because MongoDB genuinely
    is not one of these — it has no statement to guard, so `sql_guard` and
    the `sql_*` tools are typed against *this*, and handing them a Mongo
    connection is a type error rather than a runtime surprise.
    """

    async def run(
        self,
        info: ConnectionInfo,
        statement: str,
        *,
        timeout_ms: int,
        max_rows: int,
        mutating: bool = False,
        returns_rows: bool = False,
    ) -> QueryResult:
        """Execute one already-guarded statement.

        `mutating` / `returns_rows` come from the guard's parse rather than
        being re-derived here: the adapter must know whether to ask the driver
        for a row count or for a result set, and re-parsing the SQL in every
        adapter to find out would be both slower and a second place to get the
        answer wrong.
        """
        ...


__all__ = [
    "Column",
    "ConnectionInfo",
    "DbAdapter",
    "DbError",
    "QueryResult",
    "SchemaNamespace",
    "SqlAdapter",
    "Table",
]
