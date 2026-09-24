"""``enqueue_ingest`` must never fail the request that triggered it — same
disposable-infra contract as ``app/agent/session_store.py`` for Redis."""

from __future__ import annotations

import uuid

import pytest
from app.config import settings
from app.queue import _state, enqueue_ingest, get_queue


@pytest.fixture(autouse=True)
def _reset_pool() -> None:
    _state.pool = None
    yield
    _state.pool = None


async def test_enqueue_against_unreachable_redis_does_not_raise(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "redis_url", "redis://127.0.0.1:1/0")
    assert await enqueue_ingest(uuid.uuid4()) is False  # reported, not raised


async def test_get_queue_against_unreachable_redis_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "redis_url", "redis://127.0.0.1:1/0")
    with pytest.raises(Exception):  # noqa: B017 - arq/redis raise assorted connection errors
        await get_queue()


async def test_a_source_that_cannot_be_queued_says_so(
    client: object, org_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """It used to stay "pending" for good: nothing would ever pick it up."""
    from app.services.data_sources import NOT_QUEUED

    async def down(_id: uuid.UUID) -> bool:
        return False

    monkeypatch.setattr("app.services.data_sources.enqueue_ingest", down)
    a = await client.post("/api/v1/assistants", json={"name": "Q"}, headers=org_headers)  # type: ignore[attr-defined]
    aid = a.json()["id"]
    await client.post(  # type: ignore[attr-defined]
        f"/api/v1/assistants/{aid}/data-sources",
        json={"type": "text", "name": "notes", "text": "hello there"},
        headers=org_headers,
    )
    listing = await client.get(f"/api/v1/assistants/{aid}/data-sources", headers=org_headers)  # type: ignore[attr-defined]
    (row,) = listing.json()["items"]
    assert row["status"] == "error"
    assert row["error"] == NOT_QUEUED
