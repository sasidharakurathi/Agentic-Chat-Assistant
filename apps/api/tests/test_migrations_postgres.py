"""The migration chain on real Postgres (Phase 7b.9).

The unit tier runs every migration on SQLite. Postgres is where production
runs, and where some of them do something SQLite never sees: the
append-only trigger and its replacement (7a.8, 7b.7), the pgvector and
full-text indexes, partial unique indexes. This builds a scratch database,
runs the whole chain up, takes Phase 7 back down and up again, checks the
models still match, and drops it. The dev database is never touched.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import AsyncIterator

import pytest
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine
from tests.infra import unavailable
from tests.test_migrations import BEFORE_PHASE_7, alembic_at

pytestmark = [pytest.mark.integration, pytest.mark.anyio]

POSTGRES_URL = os.environ.get(
    "INTEGRATION_DATABASE_URL", "postgresql+asyncpg://app:app@localhost:45432/app"
)
#: What docker/postgres-init gives the dev database, which a new one lacks.
EXTENSIONS = ("vector", '"uuid-ossp"', "pg_trgm")


@pytest.fixture
async def scratch() -> AsyncIterator[str]:
    """A new, empty database beside the dev one; dropped afterwards."""
    admin = create_async_engine(POSTGRES_URL, isolation_level="AUTOCOMMIT")
    name = f"migtest_{uuid.uuid4().hex[:10]}"
    try:
        async with admin.connect() as conn:
            await conn.execute(text(f'CREATE DATABASE "{name}"'))
    except Exception as exc:  # pragma: no cover - environment-dependent
        await admin.dispose()
        unavailable("postgres", f"Postgres not reachable at {POSTGRES_URL}: {exc}")
    url = make_url(POSTGRES_URL).set(database=name).render_as_string(hide_password=False)
    fresh = create_async_engine(url, isolation_level="AUTOCOMMIT")
    async with fresh.connect() as conn:
        for ext in EXTENSIONS:
            await conn.execute(text(f"CREATE EXTENSION IF NOT EXISTS {ext}"))
    await fresh.dispose()
    try:
        yield url
    finally:
        async with admin.connect() as conn:
            await conn.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
        await admin.dispose()


async def test_the_chain_goes_up_back_and_up_on_postgres(scratch: str) -> None:
    for step in (
        ("upgrade", BEFORE_PHASE_7),
        ("upgrade", "head"),
        ("downgrade", BEFORE_PHASE_7),
        ("upgrade", "head"),
        ("check",),
    ):
        result = alembic_at(scratch, *step)
        # stderr only: alembic prints no connection string, but stdout of a
        # failing check lists every difference, which is what to read.
        assert result.returncode == 0, f"{step}: {result.stdout[-2000:]}{result.stderr[-2000:]}"

    engine = create_async_engine(scratch)
    try:
        async with engine.connect() as conn:
            trigger = await conn.scalar(
                text("SELECT 1 FROM pg_trigger WHERE tgname = 'audit_log_append_only'")
            )
            seals = await conn.scalar(
                text("SELECT prosrc FROM pg_proc WHERE proname = 'audit_log_append_only'")
            )
    finally:
        await engine.dispose()
    assert trigger == 1, "the append-only trigger is back after the round trip"
    assert seals is not None and "OLD.seq IS NOT NULL" in seals, "and it is 7b.7's version"
