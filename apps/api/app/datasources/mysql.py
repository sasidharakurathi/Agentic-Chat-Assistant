"""MySQL / MariaDB adapter (aiomysql)."""

from __future__ import annotations

import asyncio
import time
from contextlib import suppress
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
from app.datasources.pool import CLIENT_GRACE_SECONDS, acquire_mysql

SYSTEM_SCHEMAS = ("mysql", "information_schema", "performance_schema", "sys")

_INTROSPECT_SQL = """
SELECT c.TABLE_SCHEMA  AS table_schema,
       c.TABLE_NAME    AS table_name,
       c.COLUMN_NAME   AS column_name,
       c.COLUMN_TYPE   AS column_type,
       c.IS_NULLABLE   AS is_nullable,
       c.COLUMN_KEY    AS column_key,
       k.REFERENCED_TABLE_NAME  AS ref_table,
       k.REFERENCED_COLUMN_NAME AS ref_column,
       t.TABLE_ROWS    AS approx_rows
FROM information_schema.COLUMNS c
JOIN information_schema.TABLES t
  ON t.TABLE_SCHEMA = c.TABLE_SCHEMA AND t.TABLE_NAME = c.TABLE_NAME
LEFT JOIN information_schema.KEY_COLUMN_USAGE k
  ON k.TABLE_SCHEMA = c.TABLE_SCHEMA
 AND k.TABLE_NAME = c.TABLE_NAME
 AND k.COLUMN_NAME = c.COLUMN_NAME
 AND k.REFERENCED_TABLE_NAME IS NOT NULL
WHERE c.TABLE_SCHEMA NOT IN ('mysql', 'information_schema', 'performance_schema', 'sys')
ORDER BY c.TABLE_SCHEMA, c.TABLE_NAME, c.ORDINAL_POSITION
"""


class MysqlAdapter:
    engine = "mysql"

    async def test(self, info: ConnectionInfo) -> None:
        try:
            async with acquire_mysql(info) as conn, conn.cursor() as cur:
                await cur.execute("SELECT 1")
                await cur.fetchone()
        except Exception as exc:
            raise DbError(str(exc)) from exc

    async def introspect(self, info: ConnectionInfo) -> list[SchemaNamespace]:
        import aiomysql

        try:
            async with acquire_mysql(info) as conn, conn.cursor(aiomysql.DictCursor) as cur:
                await cur.execute(_INTROSPECT_SQL)
                rows = list(await cur.fetchall())
        except Exception as exc:
            raise DbError(str(exc)) from exc

        namespaces: dict[str, dict[str, Table]] = {}
        for row in rows:
            schema = str(row["table_schema"])
            table_name = str(row["table_name"])
            tables = namespaces.setdefault(schema, {})
            table = tables.get(table_name)
            if table is None:
                approx = row["approx_rows"]
                table = Table(
                    name=table_name,
                    approx_rows=int(approx) if approx is not None else None,
                )
                tables[table_name] = table
            ref = (
                f"{row['ref_table']}.{row['ref_column']}"
                if row.get("ref_table") and row.get("ref_column")
                else None
            )
            table.columns.append(
                Column(
                    name=str(row["column_name"]),
                    type=str(row["column_type"]),
                    nullable=row["is_nullable"] == "YES",
                    pk=row["column_key"] == "PRI",
                    fk=ref,
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
        import aiomysql

        started = time.perf_counter()
        affected: int | None = None
        try:
            async with acquire_mysql(info) as conn, conn.cursor(aiomysql.DictCursor) as cur:
                # Server-side cap, same reasoning as Postgres's
                # statement_timeout. MariaDB spells it differently and only
                # applies it to SELECTs, hence the tolerant suppress.
                with suppress(Exception):  # pragma: no cover - server-version dependent
                    await cur.execute(f"SET SESSION MAX_EXECUTION_TIME={int(timeout_ms)}")
                # MAX_EXECUTION_TIME covers read-only SELECTs only, and not at
                # all on MariaDB, so it cannot be the whole story: writes,
                # `DO SLEEP(...)` and procedure calls ran unbounded. The
                # client deadline catches them, then kills the query on the
                # server (abandoning the socket alone would leave it running)
                # and drops the connection, which is mid-protocol and cannot
                # go back to the pool.
                try:
                    await asyncio.wait_for(
                        cur.execute(statement), timeout=timeout_ms / 1000 + CLIENT_GRACE_SECONDS
                    )
                except TimeoutError:
                    await _kill_query(info, conn.thread_id())
                    conn.close()
                    raise DbError(f"statement timed out after {timeout_ms} ms") from None
                if mutating:
                    # MySQL has no RETURNING, so a write never carries rows and
                    # the driver's rowcount is the whole answer.
                    affected = int(cur.rowcount) if cur.rowcount is not None else None
                    await conn.commit()
                raw: list[Any] = list(await cur.fetchall() or [])
                columns = [d[0] for d in (cur.description or [])]
        except Exception as exc:
            raise DbError(str(exc)) from exc

        elapsed = int((time.perf_counter() - started) * 1000)
        rows, truncated = normalize_rows(raw, columns, max_rows=max_rows)
        return QueryResult(
            columns=columns,
            rows=rows,
            row_count=len(rows),
            truncated=truncated,
            elapsed_ms=elapsed,
            statement=statement,
            affected_rows=affected,
        )


__all__ = ["SYSTEM_SCHEMAS", "MysqlAdapter"]


async def _kill_query(info: ConnectionInfo, thread_id: int) -> None:
    """Stop a statement on the server from a second, short-lived connection.

    `KILL QUERY` ends the statement but keeps the session; the session is
    discarded by the caller anyway. Best effort: if this fails the query is
    still bounded by the server's own limits, and the caller has already
    decided to give up on it."""
    import aiomysql

    try:
        killer = await aiomysql.connect(
            host=info.host or "localhost",
            port=info.port or 3306,
            user=info.username,
            password=info.password or "",
            db=info.database,
            connect_timeout=5,
        )
    except Exception:  # pragma: no cover - server unreachable
        return
    try:
        async with killer.cursor() as cur:
            await cur.execute(f"KILL QUERY {int(thread_id)}")
    except Exception:  # pragma: no cover - already finished, or no privilege
        pass
    finally:
        killer.close()
