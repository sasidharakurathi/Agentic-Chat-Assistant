"""Postgres adapter (asyncpg).

Note this is a *tenant's* database, not the app's own — so it gets its own
pool (`pool.py`), its own timeouts, and no SQLAlchemy. The app's session
machinery is for the app's schema; pointing it at arbitrary user databases
would drag ORM metadata and connection reuse somewhere neither belongs.
"""

from __future__ import annotations

from typing import Any

from app.datasources.base import (
    Column,
    ConnectionInfo,
    DbError,
    QueryResult,
    SchemaNamespace,
    Table,
)
from app.datasources.normalize import normalize_rows
from app.datasources.pool import CLIENT_GRACE_SECONDS, acquire_postgres

# Server-owned catalogs. Introspection reads them, but they are never offered
# as queryable tables — a model that can SELECT from pg_catalog can enumerate
# roles and settings that have nothing to do with the user's question.
SYSTEM_SCHEMAS = ("pg_catalog", "information_schema", "pg_toast")

_INTROSPECT_SQL = """
SELECT c.table_schema,
       c.table_name,
       c.column_name,
       c.data_type,
       c.is_nullable,
       COALESCE(pk.is_pk, false)  AS is_pk,
       fk.ref                     AS fk_ref
FROM information_schema.columns c
LEFT JOIN (
    SELECT kcu.table_schema, kcu.table_name, kcu.column_name, true AS is_pk
    FROM information_schema.table_constraints tc
    JOIN information_schema.key_column_usage kcu
      ON kcu.constraint_name = tc.constraint_name
     AND kcu.table_schema = tc.table_schema
    WHERE tc.constraint_type = 'PRIMARY KEY'
) pk ON pk.table_schema = c.table_schema
    AND pk.table_name = c.table_name
    AND pk.column_name = c.column_name
LEFT JOIN (
    SELECT kcu.table_schema, kcu.table_name, kcu.column_name,
           ccu.table_name || '.' || ccu.column_name AS ref
    FROM information_schema.table_constraints tc
    JOIN information_schema.key_column_usage kcu
      ON kcu.constraint_name = tc.constraint_name
     AND kcu.table_schema = tc.table_schema
    JOIN information_schema.constraint_column_usage ccu
      ON ccu.constraint_name = tc.constraint_name
    WHERE tc.constraint_type = 'FOREIGN KEY'
) fk ON fk.table_schema = c.table_schema
    AND fk.table_name = c.table_name
    AND fk.column_name = c.column_name
WHERE c.table_schema NOT IN ('pg_catalog', 'information_schema', 'pg_toast')
ORDER BY c.table_schema, c.table_name, c.ordinal_position
"""

_ROWCOUNT_SQL = """
SELECT schemaname, relname, n_live_tup
FROM pg_stat_user_tables
"""


class PostgresAdapter:
    engine = "postgres"

    async def test(self, info: ConnectionInfo) -> None:
        try:
            async with acquire_postgres(info) as conn:
                await conn.fetchval("SELECT 1")
        except Exception as exc:
            raise DbError(str(exc)) from exc

    async def introspect(self, info: ConnectionInfo) -> list[SchemaNamespace]:
        try:
            async with acquire_postgres(info) as conn:
                rows = await conn.fetch(_INTROSPECT_SQL)
                counts = {
                    (r["schemaname"], r["relname"]): r["n_live_tup"]
                    for r in await conn.fetch(_ROWCOUNT_SQL)
                }
        except Exception as exc:
            raise DbError(str(exc)) from exc

        namespaces: dict[str, dict[str, Table]] = {}
        for row in rows:
            schema = str(row["table_schema"])
            table_name = str(row["table_name"])
            tables = namespaces.setdefault(schema, {})
            table = tables.get(table_name)
            if table is None:
                table = Table(name=table_name, approx_rows=counts.get((schema, table_name)))
                tables[table_name] = table
            table.columns.append(
                Column(
                    name=str(row["column_name"]),
                    type=str(row["data_type"]),
                    nullable=row["is_nullable"] == "YES",
                    pk=bool(row["is_pk"]),
                    fk=row["fk_ref"],
                )
            )
        return [
            SchemaNamespace(name=name, tables=list(tables.values()))
            for name, tables in sorted(namespaces.items())
        ]

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
        import time

        started = time.perf_counter()
        # A write without RETURNING yields no records, so `fetch` cannot tell
        # us how many rows it touched. asyncpg's `execute` returns the command
        # tag ("UPDATE 3") instead, which is the only place that count exists.
        # With RETURNING we fetch as usual — `execute` would throw the returned
        # rows away, and those rows *are* the count.
        count_via_tag = mutating and not returns_rows
        affected: int | None = None
        try:
            client_timeout = timeout_ms / 1000 + CLIENT_GRACE_SECONDS
            # Inside an explicit transaction, because `SET LOCAL` lasts only
            # until the end of the current one. asyncpg autocommits, so it
            # used to be discarded before the query ran: only the client
            # timeout ever fired, and a cancelled query kept burning CPU on
            # the tenant's server. The transaction also scopes the setting,
            # so the pooled connection goes back without it.
            async with acquire_postgres(info) as conn, conn.transaction():
                await conn.execute(f"SET LOCAL statement_timeout = {int(timeout_ms)}")
                if count_via_tag:
                    tag = await conn.execute(statement, timeout=client_timeout)
                    affected = _affected_from_tag(tag)
                    records: list[Any] = []
                else:
                    records = await conn.fetch(statement, timeout=client_timeout)
        except Exception as exc:
            raise DbError(str(exc)) from exc

        elapsed = int((time.perf_counter() - started) * 1000)
        columns = list(records[0].keys()) if records else []
        rows, truncated = normalize_rows([dict(r) for r in records], columns, max_rows=max_rows)
        if mutating and returns_rows:
            affected = len(rows)
        return QueryResult(
            columns=columns,
            rows=rows,
            row_count=len(rows),
            truncated=truncated,
            elapsed_ms=elapsed,
            statement=statement,
            affected_rows=affected,
        )


def _affected_from_tag(tag: str) -> int | None:
    """Pull the row count out of a Postgres command tag.

    Tags look like "UPDATE 3", "DELETE 0" or "INSERT 0 5" — the count is
    always the last token. DDL tags ("CREATE TABLE") carry no number, and
    None is the honest answer there rather than 0.
    """
    # "UPDATE 3" is the shortest countable tag: a verb and a number.
    verb_and_count = 2
    parts = (tag or "").split()
    if len(parts) < verb_and_count:
        return None
    try:
        return int(parts[-1])
    except ValueError:
        return None


__all__ = ["SYSTEM_SCHEMAS", "PostgresAdapter"]
