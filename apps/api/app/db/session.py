"""Async engine / session factory.

The engine is created lazily and cached so tests can point ``DATABASE_URL`` at
SQLite before the first use. Use :func:`get_session` as a FastAPI dependency.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from functools import lru_cache
from typing import Any

from sqlalchemy import event
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.config import settings


@lru_cache
def get_engine() -> AsyncEngine:
    url = settings.database_url
    kwargs: dict[str, object] = {"echo": False, "future": True, "pool_pre_ping": True}
    if url.startswith("sqlite"):
        # SQLite: no pooling knobs; allow use across the asyncio loop's threads.
        kwargs.pop("pool_pre_ping", None)
    engine = create_async_engine(url, **kwargs)
    if url.startswith("sqlite"):
        # SQLite ignores foreign keys — including every ON DELETE rule —
        # unless told otherwise, per connection. Without this the unit tier
        # (which runs on SQLite) had never enforced a single FK or cascade, so
        # a missing or wrong ON DELETE could not fail a test.
        event.listen(engine.sync_engine, "connect", _sqlite_enforce_foreign_keys)
    return engine


def _sqlite_enforce_foreign_keys(dbapi_connection: Any, _record: Any) -> None:
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()


@lru_cache
def get_sessionmaker() -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(
        bind=get_engine(),
        expire_on_commit=False,
        autoflush=False,
    )


async def get_session() -> AsyncIterator[AsyncSession]:
    """Yield a session, committing on success and rolling back on error."""
    session_factory = get_sessionmaker()
    async with session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


__all__ = ["get_engine", "get_session", "get_sessionmaker"]
