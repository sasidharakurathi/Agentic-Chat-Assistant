"""Connection lifecycle (task 3.2 remaining gaps).

- Shutdown closes the tenant-database pools (`close_all` was never called).
- Creating a connection verifies reachability: the background schema refresh
  queued on create connects, introspects, and records `ok` or `error` on
  the connection, without blocking the create behind a connect timeout for
  a database that is not reachable *yet*.
"""

from __future__ import annotations

import sqlite3
import tempfile
import uuid
from pathlib import Path

import pytest
from app.db.session import get_sessionmaker
from app.main import create_app, lifespan
from app.models.integration import DbConnection, DbConnectionStatus
from app.worker import refresh_schema_job
from httpx import AsyncClient

pytestmark = pytest.mark.anyio


async def test_shutdown_closes_the_tenant_pools(monkeypatch: pytest.MonkeyPatch) -> None:
    closed: list[bool] = []

    async def record() -> None:
        closed.append(True)

    monkeypatch.setattr("app.main.datasource_pool.close_all", record)
    async with lifespan(create_app()):
        assert closed == []
    assert closed == [True]


async def _create(client: AsyncClient, headers: dict[str, str], database: str) -> uuid.UUID:
    aid = (await client.post("/api/v1/assistants", json={"name": "L"}, headers=headers)).json()[
        "id"
    ]
    r = await client.post(
        f"/api/v1/assistants/{aid}/db-connections",
        json={"name": "Local", "engine": "sqlite", "database": database},
        headers=headers,
    )
    assert r.status_code == 201, r.text
    assert r.json()["status"] == "unknown", "the create itself does not wait on the network"
    return uuid.UUID(r.json()["id"])


async def _status(cid: uuid.UUID) -> tuple[DbConnectionStatus, str | None]:
    async with get_sessionmaker()() as s:
        conn = await s.get(DbConnection, cid)
        assert conn is not None
        return conn.status, conn.error


async def test_the_queued_refresh_records_a_reachable_database(
    client: AsyncClient, org_headers: dict[str, str]
) -> None:
    path = Path(tempfile.gettempdir()) / f"lifecycle-{uuid.uuid4().hex[:8]}.db"
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE t (id INTEGER PRIMARY KEY)")
    conn.commit()
    conn.close()
    try:
        cid = await _create(client, org_headers, str(path))
        await refresh_schema_job({}, str(cid))
        assert (await _status(cid))[0] is DbConnectionStatus.ok
    finally:
        path.unlink(missing_ok=True)


async def test_the_queued_refresh_records_an_unreachable_one(
    client: AsyncClient, org_headers: dict[str, str]
) -> None:
    missing = str(Path(tempfile.gettempdir()) / f"nope-{uuid.uuid4().hex}.db")
    cid = await _create(client, org_headers, missing)
    await refresh_schema_job({}, str(cid))
    status, error = await _status(cid)
    assert status is DbConnectionStatus.error
    assert error


async def test_a_refresh_for_a_deleted_connection_is_a_no_op() -> None:
    await refresh_schema_job({}, str(uuid.uuid4()))
