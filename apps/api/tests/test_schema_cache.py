"""The cached schema follows the permission profile (tasks 3.3 / 3.7).

The cache is filtered by the permissions in force when it was built, and
denied tables are never stored at all. So it can't be re-filtered later,
and nothing used to drop it when permissions changed: a newly denied
table stayed in the model's context until someone pressed refresh. Now
any change that alters what the cache describes drops it, a background
refresh is queued, and the agent refreshes a missing or expired cache on
use. `filtered` is stored with it, so "some tables are hidden" survives a
reload.

A SQLite file is the database under test: real introspection, no Docker.
"""

from __future__ import annotations

import sqlite3
import tempfile
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import anyio
import pytest
from app.agent import caps_sql
from app.db.session import get_sessionmaker
from app.models.integration import DbSchemaCache
from httpx import AsyncClient
from sqlalchemy import select, update

pytestmark = pytest.mark.anyio


@pytest.fixture
def db_file() -> Path:
    path = Path(tempfile.gettempdir()) / f"schema-cache-{uuid.uuid4().hex[:8]}.db"
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE orders (id INTEGER PRIMARY KEY, total NUMERIC)")
    conn.execute("CREATE TABLE salaries (id INTEGER PRIMARY KEY, amount NUMERIC)")
    conn.commit()
    conn.close()
    yield path
    path.unlink(missing_ok=True)


@pytest.fixture
def queued(monkeypatch: pytest.MonkeyPatch) -> list[uuid.UUID]:
    """Background refreshes that were queued (Redis is not the subject)."""
    seen: list[uuid.UUID] = []

    async def record(connection_id: uuid.UUID) -> None:
        seen.append(connection_id)

    monkeypatch.setattr("app.services.db_connections.enqueue_schema_refresh", record)
    return seen


async def _setup(client: AsyncClient, headers: dict[str, str], db_file: Path) -> tuple[str, str]:
    aid = (await client.post("/api/v1/assistants", json={"name": "S"}, headers=headers)).json()[
        "id"
    ]
    r = await client.post(
        f"/api/v1/assistants/{aid}/db-connections",
        json={"name": "Local", "engine": "sqlite", "database": str(db_file)},
        headers=headers,
    )
    assert r.status_code == 201, r.text
    return aid, r.json()["id"]


def _tables(schema: dict) -> set[str]:
    return {t["name"] for ns in schema["schemas"] for t in ns["tables"]}


async def _refresh(client: AsyncClient, headers: dict[str, str], aid: str, cid: str) -> dict:
    r = await client.post(
        f"/api/v1/assistants/{aid}/db-connections/{cid}:refresh-schema", headers=headers
    )
    assert r.status_code == 200, r.text
    return r.json()


async def _cached(client: AsyncClient, headers: dict[str, str], aid: str, cid: str) -> dict:
    r = await client.get(f"/api/v1/assistants/{aid}/db-connections/{cid}/schema", headers=headers)
    assert r.status_code == 200, r.text
    return r.json()


async def test_denying_a_table_drops_it_from_what_the_model_can_see(
    client: AsyncClient, org_headers: dict[str, str], db_file: Path, queued: list[uuid.UUID]
) -> None:
    aid, cid = await _setup(client, org_headers, db_file)
    assert queued == [uuid.UUID(cid)], "creating a connection warms the cache"
    assert _tables(await _refresh(client, org_headers, aid, cid)) == {"orders", "salaries"}

    r = await client.patch(
        f"/api/v1/assistants/{aid}/db-connections/{cid}",
        json={"permissions": {"read": True, "deny_tables": ["salaries"]}},
        headers=org_headers,
    )
    assert r.status_code == 200, r.text
    assert _tables(await _cached(client, org_headers, aid, cid)) == set(), "stale cache dropped"
    assert queued[-1] == uuid.UUID(cid), "and a refresh was queued"

    # What the agent's sql_introspect sees next:
    schema = await caps_sql._current_schema(uuid.UUID(aid), uuid.UUID(cid))
    assert {t.name for ns in schema.schemas for t in ns.tables} == {"orders"}


async def test_filtered_survives_a_reload(
    client: AsyncClient, org_headers: dict[str, str], db_file: Path, queued: list[uuid.UUID]
) -> None:
    aid, cid = await _setup(client, org_headers, db_file)
    await client.patch(
        f"/api/v1/assistants/{aid}/db-connections/{cid}",
        json={"permissions": {"read": True, "deny_tables": ["salaries"]}},
        headers=org_headers,
    )
    assert (await _refresh(client, org_headers, aid, cid))["filtered"] is True
    assert (await _cached(client, org_headers, aid, cid))["filtered"] is True, (
        "it always came back false"
    )


async def test_a_rename_keeps_the_cache(
    client: AsyncClient, org_headers: dict[str, str], db_file: Path, queued: list[uuid.UUID]
) -> None:
    aid, cid = await _setup(client, org_headers, db_file)
    await _refresh(client, org_headers, aid, cid)
    await client.patch(
        f"/api/v1/assistants/{aid}/db-connections/{cid}",
        json={"name": "Renamed"},
        headers=org_headers,
    )
    assert _tables(await _cached(client, org_headers, aid, cid)) == {"orders", "salaries"}


async def _age_cache(cid: str, days: int) -> None:
    async with get_sessionmaker()() as s:
        await s.execute(
            update(DbSchemaCache)
            .where(DbSchemaCache.db_connection_id == uuid.UUID(cid))
            .values(refreshed_at=datetime.now(UTC) - timedelta(days=days))
        )
        await s.commit()


async def test_an_expired_cache_is_refreshed_on_use(
    client: AsyncClient, org_headers: dict[str, str], db_file: Path, queued: list[uuid.UUID]
) -> None:
    aid, cid = await _setup(client, org_headers, db_file)
    await _refresh(client, org_headers, aid, cid)
    await _age_cache(cid, days=30)
    conn = sqlite3.connect(db_file)
    conn.execute("CREATE TABLE invoices (id INTEGER PRIMARY KEY)")
    conn.commit()
    conn.close()

    schema = await caps_sql._current_schema(uuid.UUID(aid), uuid.UUID(cid))
    assert "invoices" in {t.name for ns in schema.schemas for t in ns.tables}


async def test_a_stale_cache_beats_no_cache_when_refresh_fails(
    client: AsyncClient, org_headers: dict[str, str], db_file: Path, queued: list[uuid.UUID]
) -> None:
    aid, cid = await _setup(client, org_headers, db_file)
    await _refresh(client, org_headers, aid, cid)
    await _age_cache(cid, days=30)
    await anyio.Path(db_file).unlink()  # the database is gone: introspection will fail

    schema = await caps_sql._current_schema(uuid.UUID(aid), uuid.UUID(cid))
    assert {t.name for ns in schema.schemas for t in ns.tables} == {"orders", "salaries"}
    async with get_sessionmaker()() as s:
        row = await s.scalar(
            select(DbSchemaCache).where(DbSchemaCache.db_connection_id == uuid.UUID(cid))
        )
        assert row is not None
