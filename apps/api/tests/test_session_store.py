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


# ── how the chat path uses it (task 1.7) ───────────────────────────


class _FakeStore:
    """In-memory stand-in for Redis, recording writes."""

    def __init__(self) -> None:
        self.data: dict[uuid.UUID, str] = {}
        self.writes: list[tuple[uuid.UUID, str]] = []

    async def get(self, cid: uuid.UUID) -> str | None:
        return self.data.get(cid)

    async def set(self, cid: uuid.UUID, value: str) -> None:
        self.data[cid] = value
        self.writes.append((cid, value))

    async def delete(self, cid: uuid.UUID) -> None:
        self.data.pop(cid, None)


class _SessionDriver:
    """Reports a fixed SDK session and records the one it was asked to resume."""

    name = "session"

    def __init__(self, session_id: str) -> None:
        self.session_id = session_id
        self.resumed: list[str | None] = []

    async def stream(self, **kw: object) -> object:
        from app.agent.events import TokenEvent, UsageEvent

        self.resumed.append(kw.get("session_id"))  # type: ignore[arg-type]
        yield TokenEvent(text="ok")
        yield UsageEvent(tokens_in=1, tokens_out=1, sdk_session_id=self.session_id)


@pytest.fixture
def store(monkeypatch: pytest.MonkeyPatch) -> _FakeStore:
    fake = _FakeStore()
    for name in ("get", "set", "delete"):
        monkeypatch.setattr(f"app.agent.session_store.{name}", getattr(fake, name))
    return fake


async def _turn(cid: uuid.UUID) -> None:
    from app.db.session import get_sessionmaker
    from app.services import chat as chat_svc

    async with get_sessionmaker()() as session:
        async for _ in chat_svc.run_message(session, conversation_id=cid, text="hi"):
            pass


async def _conversation(client: object, headers: dict[str, str]) -> uuid.UUID:
    from tests.test_chat import _new_assistant, _new_conversation

    aid = await _new_assistant(client, headers)  # type: ignore[arg-type]
    return uuid.UUID(await _new_conversation(client, headers, aid))  # type: ignore[arg-type]


async def test_an_evicted_entry_is_written_back_by_the_next_turn(
    client: object,
    org_headers: dict[str, str],
    store: _FakeStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    driver = _SessionDriver("sdk-1")
    monkeypatch.setattr("app.agent.runtime.get_driver", lambda: driver)
    cid = await _conversation(client, org_headers)
    await _turn(cid)
    assert store.data[cid] == "sdk-1"

    store.data.clear()  # Redis restarted / the key was evicted
    await _turn(cid)
    assert driver.resumed == [None, "sdk-1"], "resumed from the row"
    assert store.data[cid] == "sdk-1", "the cache was refilled"


async def test_every_turn_rewrites_the_entry_so_its_ttl_tracks_use(
    client: object,
    org_headers: dict[str, str],
    store: _FakeStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("app.agent.runtime.get_driver", lambda: _SessionDriver("sdk-1"))
    cid = await _conversation(client, org_headers)
    for _ in range(3):
        await _turn(cid)
    assert [v for c, v in store.writes if c == cid].count("sdk-1") >= 3


async def test_a_stale_cache_never_decides_which_session_resumes(
    client: object,
    org_headers: dict[str, str],
    store: _FakeStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    driver = _SessionDriver("sdk-2")
    monkeypatch.setattr("app.agent.runtime.get_driver", lambda: driver)
    cid = await _conversation(client, org_headers)
    await _turn(cid)  # the row now holds sdk-2

    store.data[cid] = "sdk-OLD"  # a write that failed during a Redis blip
    await _turn(cid)
    assert driver.resumed[-1] == "sdk-2"
    assert store.data[cid] == "sdk-2", "and the cache was repaired"


async def test_a_cached_session_for_a_fresh_conversation_is_ignored(
    client: object,
    org_headers: dict[str, str],
    store: _FakeStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    driver = _SessionDriver("sdk-new")
    monkeypatch.setattr("app.agent.runtime.get_driver", lambda: driver)
    cid = await _conversation(client, org_headers)
    store.data[cid] = "sdk-left-over"
    await _turn(cid)
    assert driver.resumed == [None]
