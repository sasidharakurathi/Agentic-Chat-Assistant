"""Async engine / session factory.

The engine is created lazily and cached so tests can point ``DATABASE_URL`` at
SQLite before the first use. Use :func:`get_session` as a FastAPI dependency.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from functools import lru_cache

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
    return create_async_engine(url, **kwargs)


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
