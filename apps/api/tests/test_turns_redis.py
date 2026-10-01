"""The turn log in Redis (QOS-01), against a real Redis.

The unit tier runs turns on the in-memory log; this is the one a
deployment uses, where a page can watch a turn through any API process.
Every key is made up for the test and deleted after it.
"""

from __future__ import annotations

import asyncio
import contextlib
import uuid
from collections.abc import AsyncIterator

import pytest
from app.db.redis import get_redis
from app.services import turns

pytestmark = [pytest.mark.integration, pytest.mark.anyio]


@pytest.fixture
async def redis_log(monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[turns.RedisLog]:
    get_redis.cache_clear()
    try:
        await get_redis().ping()
    except Exception as exc:  # pragma: no cover - environment-dependent
        pytest.skip(f"Redis not reachable: {type(exc).__name__}")
    # Short waits, so a test about waiting does not take five seconds.
    monkeypatch.setattr(turns, "BLOCK_S", 0.3)
    # Every key the log uses, to delete afterwards: the real names, recorded.
    made: list[str] = []
    real_key = turns._events_key

    def recorded(conversation_id: str, turn_id: str) -> str:
        made.append(real_key(conversation_id, turn_id))
        return made[-1]

    monkeypatch.setattr(turns, "_events_key", recorded)
    yield turns.RedisLog()
    redis = get_redis()
    with contextlib.suppress(Exception):
        await redis.delete(*made) if made else None
    get_redis.cache_clear()


async def _read(stream: AsyncIterator[tuple[str, str]]) -> list[tuple[str, str]]:
    return [item async for item in stream]


async def test_a_turn_is_written_watched_and_ended(redis_log: turns.RedisLog) -> None:
    cid, tid = str(uuid.uuid4()), uuid.uuid4().hex
    assert await redis_log.claim(cid, tid)
    assert not await redis_log.claim(cid, uuid.uuid4().hex), "one turn per conversation"
    assert await redis_log.live(cid) == tid

    watcher = asyncio.create_task(_read(redis_log.follow(cid, tid, None)))
    for word in ("one", "two", "three"):
        await redis_log.append(cid, tid, f"data: {word}\n\n")
        await asyncio.sleep(0.05)
    await redis_log.finish(cid, tid)
    await redis_log.release(cid, tid)
    seen = await asyncio.wait_for(watcher, 5)
    assert [frame for _id, frame in seen] == ["data: one\n\n", "data: two\n\n", "data: three\n\n"]
    assert await redis_log.live(cid) is None

    # Readable again after it ended, from the start or after an event.
    again = await _read(redis_log.follow(cid, tid, None))
    assert again == seen
    after_first = await _read(redis_log.follow(cid, tid, seen[0][0]))
    assert after_first == seen[1:]
    # It expires on its own.
    ttl = await get_redis().ttl(f"turn:events:{cid}:{tid}")
    assert 0 < ttl <= turns.KEEP_S


async def test_a_turn_whose_process_stopped_is_reported_lost(redis_log: turns.RedisLog) -> None:
    cid, tid = str(uuid.uuid4()), uuid.uuid4().hex
    assert await redis_log.claim(cid, tid)
    await redis_log.append(cid, tid, "data: half\n\n")
    # The process dies: no end marker, and its claim lapses.
    await get_redis().delete(f"turn:live:{cid}")
    seen = await asyncio.wait_for(_read(redis_log.follow(cid, tid, None)), 5)
    assert [frame for _id, frame in seen] == ["data: half\n\n", turns.LOST]


async def test_a_claim_expires_unless_the_turn_keeps_beating(redis_log: turns.RedisLog) -> None:
    cid, tid = str(uuid.uuid4()), uuid.uuid4().hex
    assert await redis_log.claim(cid, tid)
    assert 0 < await get_redis().ttl(f"turn:live:{cid}") <= turns.LIVE_TTL_S
    await get_redis().expire(f"turn:live:{cid}", 2)
    await redis_log.heartbeat(cid, tid)
    assert await get_redis().ttl(f"turn:live:{cid}") > 2
    # Someone else's turn is not released, or refreshed, by this one.
    await redis_log.release(cid, uuid.uuid4().hex)
    assert await redis_log.live(cid) == tid
    await redis_log.release(cid, tid)


async def test_a_turn_is_known_only_under_its_own_conversation(
    redis_log: turns.RedisLog,
) -> None:
    cid, tid = str(uuid.uuid4()), uuid.uuid4().hex
    assert await redis_log.claim(cid, tid)
    await redis_log.append(cid, tid, "data: x\n\n")
    await redis_log.finish(cid, tid)
    await redis_log.release(cid, tid)
    assert await redis_log.known(cid, tid)
    assert not await redis_log.known(str(uuid.uuid4()), tid)
    assert await _read(redis_log.follow(str(uuid.uuid4()), tid, None)) == [("lost", turns.LOST)], (
        "nothing of the turn under another conversation"
    )
