"""SQLite adapter (aiosqlite).

No pool: SQLite connections are file handles, not sockets, so opening one per
query costs microseconds and avoids every cross-task locking problem a shared
handle would introduce.

Opened **read-only** by default via a URI (`mode=ro`), which is a second line
of defence underneath the SQL guard: even a statement that somehow got past
classification cannot write through a read-only handle.
"""

from __future__ import annotations

import time
from typing import Any

import aiosqlite

from app.datasources.base import (
    Column,
    ConnectionInfo,
    DbError,
    QueryResult,
    SchemaNamespace,
    Table,
)
from app.datasources.normalize import normalize_rows

#: VM instructions between deadline checks: small enough to stop within a
#: few milliseconds, large enough that the check itself costs nothing.
_PROGRESS_STEPS = 10_000


def _uri(info: ConnectionInfo, *, writable: bool) -> str:
    from urllib.parse import quote

    mode = "rw" if writable else "ro"
    return f"file:{quote(info.database)}?mode={mode}"


class SqliteAdapter:
    engine = "sqlite"

    async def test(self, info: ConnectionInfo) -> None:
        try:
            async with aiosqlite.connect(_uri(info, writable=False), uri=True) as conn:
                await conn.execute("SELECT 1")
        except Exception as exc:
            raise DbError(str(exc)) from exc

    async def introspect(self, info: ConnectionInfo) -> list[SchemaNamespace]:
        try:
            async with aiosqlite.connect(_uri(info, writable=False), uri=True) as conn:
                conn.row_factory = aiosqlite.Row
                cursor = await conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' "
                    "AND name NOT LIKE 'sqlite_%' ORDER BY name"
                )
                table_names = [str(r["name"]) for r in await cursor.fetchall()]

                tables: list[Table] = []
                for name in table_names:
                    # PRAGMA can't be parameterised; the name came from
                    # sqlite_master (not from a user), and quotes are doubled.
                    safe = name.replace('"', '""')
                    cols = await (await conn.execute(f'PRAGMA table_info("{safe}")')).fetchall()
                    fk_cursor = await conn.execute(f'PRAGMA foreign_key_list("{safe}")')
                    fks = await fk_cursor.fetchall()
                    fk_by_col = {str(r["from"]): f"{r['table']}.{r['to']}" for r in fks}
                    tables.append(
                        Table(
                            name=name,
                            columns=[
                                Column(
                                    name=str(c["name"]),
                                    type=str(c["type"] or "").lower() or "any",
                                    nullable=not bool(c["notnull"]),
                                    pk=bool(c["pk"]),
                                    fk=fk_by_col.get(str(c["name"])),
                                )
                                for c in cols
                            ],
                        )
                    )
        except Exception as exc:
            raise DbError(str(exc)) from exc

        # SQLite has no schemas; "main" keeps the normalized shape uniform.
        return [SchemaNamespace(name="main", tables=tables)]

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
        started = time.perf_counter()
        writable = bool(info.options.get("writable"))
        affected: int | None = None
        try:
            async with aiosqlite.connect(
                _uri(info, writable=writable), uri=True, timeout=timeout_ms / 1000
            ) as conn:
                conn.row_factory = aiosqlite.Row
                # `timeout=` above is how long to wait for a *lock*, not how
                # long a statement may run: a runaway query ran forever, and
                # cancelling the coroutine cannot stop aiosqlite's worker
                # thread. SQLite's progress handler runs inside the engine
                # every N VM steps; returning non-zero aborts the statement
                # ("interrupted"), which is the only reliable stop.
                deadline = time.monotonic() + timeout_ms / 1000
                await conn.set_progress_handler(
                    lambda: 1 if time.monotonic() > deadline else 0, _PROGRESS_STEPS
                )
                cursor = await conn.execute(statement)
                raw: list[Any] = list(await cursor.fetchall())
                if mutating:
                    # sqlite3 reports -1 for statements it does not count
                    # (DDL); None is the honest answer rather than -1.
                    rc = cursor.rowcount
                    affected = int(rc) if rc is not None and rc >= 0 else None
                if writable:
                    await conn.commit()
                columns = [d[0] for d in (cursor.description or [])]
        except Exception as exc:
            if "interrupted" in str(exc):
                raise DbError(f"statement timed out after {timeout_ms} ms") from exc
            raise DbError(str(exc)) from exc

        elapsed = int((time.perf_counter() - started) * 1000)
        rows, truncated = normalize_rows([dict(r) for r in raw], columns, max_rows=max_rows)
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


__all__ = ["SqliteAdapter"]
