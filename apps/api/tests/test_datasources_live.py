"""Adapters against real database servers (tasks 3.2 / 3.3 / 3.5 / 3.6).

Integration tier. Postgres is the one that's always up in this project;
MySQL and MongoDB come from the compose `datastores` profile:

    docker compose --profile datastores up -d mysql mongo

Each engine skips cleanly if its server isn't reachable, so a partial dev
environment doesn't turn into a wall of red.

The point of testing against real servers rather than mocks: introspection is
a pile of engine-specific catalog SQL, and normalization exists precisely
because every driver returns its own types. Both are the kind of code that
looks right and is wrong.
"""

from __future__ import annotations

import os
import sqlite3
import tempfile
import uuid
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from app.datasources import ConnectionInfo, DbError, get_mongo_adapter, get_sql_adapter
from app.datasources.mongodb import MongoBlocked
from app.datasources.pool import close_all
from app.datasources.sql_guard import Permissions, guard

pytestmark = pytest.mark.integration

PG = ConnectionInfo(
    engine="postgres",
    host="localhost",
    port=int(os.environ.get("PGPORT", "45432")),
    database="app",
    username="app",
    password="app",
)
MYSQL = ConnectionInfo(
    engine="mysql",
    host="localhost",
    port=int(os.environ.get("MYSQL_PORT", "43306")),
    database="appdb",
    username="root",
    password="rootpw",
)
MONGO = ConnectionInfo(
    engine="mongodb",
    host="localhost",
    port=int(os.environ.get("MONGO_PORT", "47017")),
    database="appdb",
    options={"uri": "mongodb://root:rootpw@localhost:47017/?authSource=admin"},
)


@pytest.fixture(autouse=True)
async def _close_pools() -> AsyncIterator[None]:
    yield
    await close_all()


async def _skip_unless_reachable(info: ConnectionInfo) -> None:
    adapter = get_mongo_adapter() if info.engine == "mongodb" else get_sql_adapter(info.engine)
    try:
        await adapter.test(info)
    except DbError as exc:  # pragma: no cover - environment-dependent
        pytest.skip(f"{info.engine} not reachable: {exc}")


# ── postgres ─────────────────────────────────────────────────


async def test_postgres_introspects_the_apps_own_schema() -> None:
    await _skip_unless_reachable(PG)
    namespaces = await get_sql_adapter("postgres").introspect(PG)
    tables = {t.name for ns in namespaces for t in ns.tables}
    assert {"organizations", "assistants", "db_connections"} <= tables
    # System catalogs must not appear as queryable tables.
    assert not any(ns.name in ("pg_catalog", "information_schema") for ns in namespaces)

    orgs = next(t for ns in namespaces for t in ns.tables if t.name == "organizations")
    pk = [c.name for c in orgs.columns if c.pk]
    assert pk == ["id"]


async def test_postgres_runs_a_guarded_query_and_normalizes_types() -> None:
    """Decimal must arrive as a string, not a float — money is the most common
    decimal column and 0.1 + 0.2 is the wrong answer to show someone."""
    await _skip_unless_reachable(PG)
    guarded = guard(
        "SELECT 1 AS n, 1.25::numeric AS money, now() AS ts, gen_random_uuid() AS id",
        engine="postgres",
        permissions=Permissions(read=True, row_limit=10),
    )
    result = await get_sql_adapter("postgres").run(
        PG, guarded.statement, timeout_ms=5000, max_rows=10
    )
    (row,) = result.rows
    assert row[0] == 1
    assert row[1] == "1.25"
    assert isinstance(row[2], str) and "T" in row[2]
    assert isinstance(row[3], str) and len(row[3]) == 36


async def test_the_injected_limit_actually_caps_the_server_side_result() -> None:
    await _skip_unless_reachable(PG)
    guarded = guard(
        "SELECT generate_series(1, 1000) AS n",
        engine="postgres",
        permissions=Permissions(read=True, row_limit=7),
    )
    result = await get_sql_adapter("postgres").run(
        PG, guarded.statement, timeout_ms=5000, max_rows=500
    )
    assert result.row_count == 7


async def test_a_statement_timeout_is_enforced_by_the_server() -> None:
    """The client-side timeout alone would leave the query burning CPU on the
    tenant's box. This used to pass anyway: `SET LOCAL` ran outside any
    transaction (asyncpg autocommits), so it was discarded at once and only
    the client timeout fired. The server's own error is the proof."""
    await _skip_unless_reachable(PG)
    with pytest.raises(DbError, match="statement timeout"):
        await get_sql_adapter("postgres").run(PG, "SELECT pg_sleep(5)", timeout_ms=300, max_rows=1)


async def test_the_postgres_timeout_does_not_leak_into_the_pool() -> None:
    """`SET LOCAL` must stay inside its transaction: a pooled connection
    handed to the next query must not inherit someone else's 300 ms cap."""
    await _skip_unless_reachable(PG)
    adapter = get_sql_adapter("postgres")
    await adapter.run(PG, "SELECT 1", timeout_ms=300, max_rows=1)
    result = await adapter.run(PG, "SHOW statement_timeout", timeout_ms=60_000, max_rows=1)
    assert result.rows == [["1min"]]


async def _elapsed(coro: object) -> float:
    import asyncio
    import time

    started = time.perf_counter()
    with pytest.raises(DbError):
        await asyncio.wait_for(coro, timeout=10)  # type: ignore[arg-type]
    return time.perf_counter() - started


# ── sqlite ───────────────────────────────────────────────────


@pytest.fixture
def sqlite_file() -> Path:
    path = Path(tempfile.gettempdir()) / f"ds-test-{uuid.uuid4().hex[:8]}.db"
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE widgets (id INTEGER PRIMARY KEY, name TEXT NOT NULL, price NUMERIC)")
    conn.execute("INSERT INTO widgets (name, price) VALUES ('bolt', 1.5), ('nut', 0.75)")
    conn.commit()
    conn.close()
    yield path
    path.unlink(missing_ok=True)


async def test_sqlite_introspects_and_reads(sqlite_file: Path) -> None:
    info = ConnectionInfo(engine="sqlite", database=str(sqlite_file))
    adapter = get_sql_adapter("sqlite")
    (ns,) = await adapter.introspect(info)
    (table,) = ns.tables
    assert table.name == "widgets"
    assert [c.name for c in table.columns] == ["id", "name", "price"]
    assert [c.name for c in table.columns if c.pk] == ["id"]

    result = await adapter.run(info, "SELECT * FROM widgets", timeout_ms=5000, max_rows=10)
    assert result.rows == [[1, "bolt", 1.5], [2, "nut", 0.75]]


async def test_sqlite_statements_time_out(sqlite_file: Path) -> None:
    """`timeout=` on connect is SQLite's *lock-wait* timeout, not a statement
    timeout: a runaway query used to run for as long as it liked."""
    info = ConnectionInfo(engine="sqlite", database=str(sqlite_file))
    runaway = (
        "WITH RECURSIVE n(i) AS (SELECT 1 UNION ALL SELECT i + 1 FROM n) SELECT count(*) FROM n"
    )
    took = await _elapsed(get_sql_adapter("sqlite").run(info, runaway, timeout_ms=300, max_rows=1))
    assert took < 3, f"ran for {took:.1f}s against a 0.3s timeout"


async def test_sqlite_is_opened_read_only_by_default(sqlite_file: Path) -> None:
    """A second line of defence *underneath* the guard: even a statement that
    somehow got past classification cannot write through a read-only handle."""
    info = ConnectionInfo(engine="sqlite", database=str(sqlite_file))
    with pytest.raises(DbError):
        await get_sql_adapter("sqlite").run(
            info, "DELETE FROM widgets", timeout_ms=5000, max_rows=10
        )
    # ...and the rows are still there.
    after = await get_sql_adapter("sqlite").run(
        info, "SELECT count(*) AS n FROM widgets", timeout_ms=5000, max_rows=10
    )
    assert after.rows == [[2]]


# ── mysql ────────────────────────────────────────────────────


async def test_mysql_statements_time_out() -> None:
    """MAX_EXECUTION_TIME covers SELECTs on MySQL only (MariaDB spells it
    differently), and setting it failed silently. A client-side deadline
    now backs it, and the stuck query is killed on the server."""
    await _skip_unless_reachable(MYSQL)
    took = await _elapsed(
        get_sql_adapter("mysql").run(MYSQL, "DO SLEEP(5)", timeout_ms=300, max_rows=1)
    )
    assert took < 3, f"ran for {took:.1f}s against a 0.3s timeout"


async def test_mysql_introspects_and_reads() -> None:
    await _skip_unless_reachable(MYSQL)
    adapter = get_sql_adapter("mysql")
    from app.datasources.pool import acquire_mysql

    async with acquire_mysql(MYSQL) as conn, conn.cursor() as cur:
        await cur.execute("DROP TABLE IF EXISTS parts")
        await cur.execute(
            "CREATE TABLE parts (id INT PRIMARY KEY, label VARCHAR(50) NOT NULL, cost DECIMAL(6,2))"
        )
        await cur.execute("INSERT INTO parts VALUES (1,'alpha',9.99),(2,'beta',0.50)")

    namespaces = await adapter.introspect(MYSQL)
    parts = next(t for ns in namespaces for t in ns.tables if t.name == "parts")
    assert [c.name for c in parts.columns] == ["id", "label", "cost"]
    assert [c.name for c in parts.columns if c.pk] == ["id"]

    guarded = guard(
        "SELECT * FROM parts", engine="mysql", permissions=Permissions(read=True, row_limit=10)
    )
    result = await adapter.run(MYSQL, guarded.statement, timeout_ms=5000, max_rows=10)
    assert result.rows == [[1, "alpha", "9.99"], [2, "beta", "0.50"]]


# ── mongodb ──────────────────────────────────────────────────


async def test_mongo_find_aggregate_and_inferred_schema() -> None:
    await _skip_unless_reachable(MONGO)
    adapter = get_mongo_adapter()
    from app.datasources.pool import get_mongo_client

    client = await get_mongo_client(MONGO)
    coll = client["appdb"]["gadgets"]
    await coll.delete_many({})
    await coll.insert_many([{"sku": "A1", "qty": 5}, {"sku": "B2", "qty": 9}])

    (ns,) = await adapter.introspect(MONGO)
    gadgets = next(t for t in ns.tables if t.name == "gadgets")
    assert {"sku", "qty", "_id"} <= {c.name for c in gadgets.columns}

    found = await adapter.find(
        MONGO,
        collection="gadgets",
        query={"qty": {"$gt": 4}},
        projection=None,
        limit=10,
        timeout_ms=5000,
    )
    assert found.row_count == 2

    agg = await adapter.aggregate(
        MONGO,
        collection="gadgets",
        pipeline=[{"$group": {"_id": None, "total": {"$sum": "$qty"}}}],
        limit=10,
        timeout_ms=5000,
    )
    assert agg.rows[0][1] == 14


@pytest.mark.parametrize(
    "pipeline",
    [
        [{"$out": "stolen"}],
        [{"$merge": {"into": "stolen"}}],
        [{"$match": {"$where": "1 == 1"}}],
        [{"$group": {"_id": None, "x": {"$accumulator": {}}}}],
    ],
)
async def test_mongo_write_and_javascript_stages_are_refused(pipeline: list[dict]) -> None:
    """These turn a read into a write or into server-side JS. Blocked before
    the pipeline reaches the server at all."""
    await _skip_unless_reachable(MONGO)
    with pytest.raises(MongoBlocked):
        await get_mongo_adapter().aggregate(
            MONGO, collection="gadgets", pipeline=pipeline, limit=1, timeout_ms=2000
        )


async def test_mongo_aggregate_limit_is_appended_not_trusted() -> None:
    """A pipeline can `$unwind` its way well past whatever limit it declared
    earlier, so the cap is appended rather than taken on trust."""
    await _skip_unless_reachable(MONGO)
    result = await get_mongo_adapter().aggregate(
        MONGO,
        collection="gadgets",
        pipeline=[{"$limit": 100}, {"$project": {"sku": 1}}],
        limit=1,
        timeout_ms=5000,
    )
    assert result.row_count == 1


async def test_a_wrong_credential_never_rides_on_another_connections_client() -> None:
    """P0-5d. The pool key left out `options`, where a Mongo connection string
    (credentials included) lives. Two connections to the same host differing
    only in their URI therefore shared one client — and the second silently
    used the first one's login. pymongo authenticates on connect, so a fresh
    client with a wrong password fails even `ping`; one riding on a cached
    good client does not."""
    await _skip_unless_reachable(MONGO)  # opens and caches a correctly authenticated client
    wrong = ConnectionInfo(
        engine="mongodb",
        host=MONGO.host,
        port=MONGO.port,
        database=MONGO.database,
        options={"uri": str(MONGO.options["uri"]).replace("rootpw", "wrong-password")},
    )
    with pytest.raises(DbError):
        await get_mongo_adapter().test(wrong)


async def test_limit_zero_never_reaches_the_server() -> None:
    """P0-5c. pymongo reads `limit=0` as *no limit*; the adapter refuses it
    rather than stream the whole collection into the API process."""
    await _skip_unless_reachable(MONGO)
    with pytest.raises(MongoBlocked):
        await get_mongo_adapter().find(
            MONGO, collection="gadgets", query={}, projection=None, limit=0, timeout_ms=5000
        )


# ── affected-row counts ──────────────────────────────────────
#
# Regression. `row_count` counts rows *returned*, and an UPDATE without
# RETURNING returns none — so every write used to report "0 row(s) affected"
# no matter how many it changed. That string is shown to whoever just
# approved the write, and "0 rows affected" reads as "nothing happened",
# which invites running it again.


async def _pg_write(sql: str) -> object:
    guarded = guard(
        sql, engine="postgres", permissions=Permissions(read=True, write=True, ddl=True)
    )
    return await get_sql_adapter("postgres").run(
        PG,
        guarded.statement,
        timeout_ms=5000,
        max_rows=100,
        mutating=guarded.mutating,
        returns_rows=guarded.returns_rows,
    )


@pytest.fixture
async def pg_scratch() -> AsyncIterator[str]:
    name = f"t_{uuid.uuid4().hex[:8]}"
    await _pg_write(f"CREATE TABLE {name} (id int, note text)")
    try:
        yield name
    finally:
        await _pg_write(f"DROP TABLE IF EXISTS {name}")


async def test_postgres_reports_how_many_rows_a_write_touched(pg_scratch: str) -> None:
    await _skip_unless_reachable(PG)
    ins = await _pg_write(f"INSERT INTO {pg_scratch} (id, note) VALUES (1,'a'),(2,'b'),(3,'c')")
    assert ins.affected_rows == 3

    upd = await _pg_write(f"UPDATE {pg_scratch} SET note='x' WHERE id <= 2")
    assert upd.affected_rows == 2
    assert upd.row_count == 0  # nothing was *returned*; the two are different facts

    nothing = await _pg_write(f"UPDATE {pg_scratch} SET note='y' WHERE id = 999")
    assert nothing.affected_rows == 0  # a genuine zero, distinguishable from the bug

    dele = await _pg_write(f"DELETE FROM {pg_scratch} WHERE id = 1")
    assert dele.affected_rows == 1


async def test_returning_rows_survive_and_still_count(pg_scratch: str) -> None:
    """The count for a write comes from the command tag, which `execute`
    gives us but throws rows away to get. A RETURNING write must therefore
    take the other path — otherwise the fix would silently eat its output."""
    await _skip_unless_reachable(PG)
    await _pg_write(f"INSERT INTO {pg_scratch} (id, note) VALUES (1,'a'),(2,'b')")
    res = await _pg_write(f"UPDATE {pg_scratch} SET note='z' RETURNING id, note")
    assert res.affected_rows == 2
    assert sorted(r[0] for r in res.rows) == [1, 2]
    assert res.columns == ["id", "note"]


async def test_reads_report_no_affected_count(pg_scratch: str) -> None:
    await _skip_unless_reachable(PG)
    res = await _pg_write(f"SELECT * FROM {pg_scratch}")
    assert res.affected_rows is None


async def test_sqlite_reports_affected_rows(sqlite_file: Path) -> None:
    info = ConnectionInfo(engine="sqlite", database=str(sqlite_file), options={"writable": True})
    guarded = guard(
        "UPDATE widgets SET price = 2.0",
        engine="sqlite",
        permissions=Permissions(read=True, write=True),
    )
    res = await get_sql_adapter("sqlite").run(
        info,
        guarded.statement,
        timeout_ms=5000,
        max_rows=10,
        mutating=guarded.mutating,
        returns_rows=guarded.returns_rows,
    )
    assert res.affected_rows == 2


async def test_mysql_reports_affected_rows() -> None:
    await _skip_unless_reachable(MYSQL)
    from app.datasources.pool import acquire_mysql

    table = f"t_{uuid.uuid4().hex[:8]}"
    async with acquire_mysql(MYSQL) as conn, conn.cursor() as cur:
        await cur.execute(f"CREATE TABLE {table} (id INT, note VARCHAR(10))")
        await cur.execute(f"INSERT INTO {table} VALUES (1,'a'),(2,'b'),(3,'c')")
        await conn.commit()

    try:
        guarded = guard(
            f"UPDATE {table} SET note='x' WHERE id <= 2",
            engine="mysql",
            permissions=Permissions(read=True, write=True),
        )
        res = await get_sql_adapter("mysql").run(
            MYSQL,
            guarded.statement,
            timeout_ms=5000,
            max_rows=10,
            mutating=guarded.mutating,
            returns_rows=guarded.returns_rows,
        )
        assert res.affected_rows == 2
    finally:
        async with acquire_mysql(MYSQL) as conn, conn.cursor() as cur:
            await cur.execute(f"DROP TABLE IF EXISTS {table}")
            await conn.commit()
