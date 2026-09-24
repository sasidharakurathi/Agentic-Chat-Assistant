"""Cross-worker pub/sub listeners against a real Redis.

The interrupt and approval listeners shared the command client, whose 1s
socket timeout made an idle subscriber time out, unsubscribe and sleep 2s,
over and over. Pub/sub keeps nothing for an absent subscriber, so a message
published in that window was lost. Each test here publishes only after the
listener has been idle for longer than that old timeout.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator
from typing import Any

import pytest
from app.agent import approval_registry, interrupts
from app.config import settings
from app.db import redis as redis_mod
from app.db.redis import get_redis
from redis.asyncio import Redis

pytestmark = [pytest.mark.integration, pytest.mark.anyio]

#: Longer than the old 1s socket timeout, so the old listener would be
#: mid-reconnect when the message arrives.
IDLE_S = 1.6


@pytest.fixture
async def publisher() -> AsyncIterator[Redis]:
    # `get_redis()` is cached per process, and each test gets its own event
    # loop: a client an earlier test created belongs to a closed loop, and
    # `interrupts.publish` through it fails. One loop per process in
    # production, so this is a test-harness concern only.
    get_redis.cache_clear()
    client = Redis.from_url(settings.redis_url, decode_responses=True, socket_connect_timeout=1.0)
    try:
        await client.ping()
    except Exception:
        pytest.skip(f"Redis not reachable at {settings.redis_url}")
    yield client
    await client.aclose()


async def _subscribed(client: Redis, channel: str) -> None:
    for _ in range(50):
        counts = dict(await client.pubsub_numsub(channel))
        if counts.get(channel, 0) >= 1:
            return
        await asyncio.sleep(0.05)
    raise AssertionError(f"nobody subscribed to {channel}")


async def test_an_idle_subscriber_still_receives(
    publisher: Redis, monkeypatch: pytest.MonkeyPatch
) -> None:
    channel = f"test:{uuid.uuid4().hex}"
    got: list[dict[str, Any]] = []
    warnings: list[str] = []
    monkeypatch.setattr(redis_mod.log, "warning", lambda event, **_k: warnings.append(event))
    task = asyncio.create_task(redis_mod.subscribe_forever(channel, got.append, name="test"))
    try:
        await _subscribed(publisher, channel)
        for n in range(3):
            await asyncio.sleep(IDLE_S)
            await publisher.publish(channel, f'{{"n": {n}}}')
        for _ in range(40):
            if len(got) == 3:
                break
            await asyncio.sleep(0.05)
    finally:
        task.cancel()
    assert got == [{"n": 0}, {"n": 1}, {"n": 2}]
    assert warnings == [], "an idle subscription must not reconnect"


async def test_bad_messages_and_a_failing_handler_do_not_end_the_subscription(
    publisher: Redis,
) -> None:
    channel = f"test:{uuid.uuid4().hex}"
    got: list[dict[str, Any]] = []

    def handle(payload: dict[str, Any]) -> None:
        if payload.get("boom"):
            raise RuntimeError("handler failed")
        got.append(payload)

    task = asyncio.create_task(redis_mod.subscribe_forever(channel, handle, name="test"))
    try:
        await _subscribed(publisher, channel)
        for raw in ("not json", "[1, 2]", '{"boom": true}', '{"ok": 1}'):
            await publisher.publish(channel, raw)
        for _ in range(40):
            if got:
                break
            await asyncio.sleep(0.05)
    finally:
        task.cancel()
    assert got == [{"ok": 1}]


async def test_an_interrupt_from_another_worker_arrives_after_a_quiet_spell(
    publisher: Redis,
) -> None:
    cid = uuid.uuid4()
    stopped = asyncio.Event()
    interrupts.register(cid, stopped.set)
    await interrupts.start_listener()
    try:
        await _subscribed(publisher, interrupts.CHANNEL)
        await asyncio.sleep(IDLE_S)
        await interrupts.publish(cid)
        await asyncio.wait_for(stopped.wait(), 2.0)
    finally:
        interrupts.unregister(cid, stopped.set)
        await interrupts.stop_listener()


async def test_an_unknown_decision_is_a_denial(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[tuple[str, str]] = []
    monkeypatch.setattr(approval_registry, "resolve_local", lambda a, d: seen.append((a, d)))
    approval_registry._on_published({"approval_id": "a1", "decision": "approved"})
    approval_registry._on_published({"approval_id": "a2", "decision": "yes please"})
    approval_registry._on_published({"approval_id": "a3"})
    assert seen == [("a1", "approved"), ("a2", "denied"), ("a3", "denied")]
