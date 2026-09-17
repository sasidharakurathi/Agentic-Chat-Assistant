"""``session_store`` must never blow up a turn just because Redis is down —
these tests point it at an address nothing is listening on and confirm every
operation degrades to a no-op instead of raising."""

from __future__ import annotations

import uuid

import pytest
from app.agent import session_store
from app.config import settings
from app.db.redis import get_redis


@pytest.fixture(autouse=True)
def _unreachable_redis(monkeypatch: pytest.MonkeyPatch) -> None:
    # Port 1 is a reserved port nothing binds to; the client should fail fast
    # (socket_connect_timeout=1.0, set in get_redis) rather than hang.
    monkeypatch.setattr(settings, "redis_url", "redis://127.0.0.1:1/0")
    get_redis.cache_clear()
    yield
    get_redis.cache_clear()


async def test_get_against_unreachable_redis_returns_none() -> None:
    assert await session_store.get(uuid.uuid4()) is None


async def test_set_against_unreachable_redis_does_not_raise() -> None:
    await session_store.set(uuid.uuid4(), "some-sdk-session-id")
