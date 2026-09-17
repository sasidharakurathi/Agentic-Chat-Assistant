"""Async Redis client, cached like the SQLAlchemy engine in ``db/session.py``.

Nothing here talks to Redis at import time — the connection pool is created
lazily on first use so tests never need Redis to be running.
"""

from __future__ import annotations

from functools import lru_cache

from redis.asyncio import Redis

from app.config import settings


@lru_cache
def get_redis() -> Redis:
    return Redis.from_url(
        settings.redis_url,
        decode_responses=True,
        socket_connect_timeout=1.0,
        socket_timeout=1.0,
    )


__all__ = ["get_redis"]
