"""Load and tuning (task 6.5).

What the load test found, held in place: a chat holds no database
connection while it waits for a slot or for the model; the pool is sized by
settings; a vector search asks the index for as many candidates as it
wants rows; a repeated query is embedded once. And the load-test script's
own arithmetic.

The index settings themselves only exist in Postgres: that part is in the
integration tier (`-m integration`).
"""

from __future__ import annotations

import asyncio
import os
import time
import uuid
from collections.abc import AsyncIterator
from typing import Any

import pytest
from app.agent.events import AgentEvent, ErrorEvent, TokenEvent
from app.config import settings
from app.db import session as db_session_module
from app.db.session import get_engine, get_sessionmaker
from app.observability import collect, metrics
from app.rag import retrieve
from app.rag.vectorstore import pgvector
from app.services import chat
from httpx import AsyncClient
from scripts import loadtest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

pytestmark = pytest.mark.anyio

API = "/api/v1"


@pytest.fixture(autouse=True)
def _clean() -> Any:
    metrics.reset()
    collect.clear_cache()
    retrieve.clear_query_cache()
    chat._slots.clear()
    yield
    retrieve.clear_query_cache()
    chat._slots.clear()


async def _conversation(client: AsyncClient, headers: dict[str, str]) -> str:
    a = (await client.post(f"{API}/assistants", json={"name": "Shop"}, headers=headers)).json()
    conv = await client.post(f"{API}/assistants/{a['id']}/conversations", json={}, headers=headers)
    return str(conv.json()["id"])


async def _drain(events: AsyncIterator[AgentEvent]) -> list[AgentEvent]:
    return [e async for e in events]


# ── connections ──────────────────────────────────────────────


async def test_a_turn_holds_no_connection_while_the_model_is_answering(
    client: AsyncClient, org_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Before: the turn's session sat in an open transaction for the whole
    answer. Eight two-second turns kept eight connections; thirty-two
    exhausted the pool and 98% of them failed."""
    cid = uuid.UUID(await _conversation(client, org_headers))
    monkeypatch.setattr(settings, "agent_fake_delay_ms", 400)
    async with get_sessionmaker()() as s:
        turn = asyncio.create_task(_drain(chat.run_message(s, conversation_id=cid, text="hello")))
        await asyncio.sleep(0.2)
        assert chat.turn_load == {"running": 1, "waiting": 0}
        assert not s.in_transaction(), "the turn is holding a connection while it streams"
        events = await turn
    assert any(isinstance(e, TokenEvent) for e in events)
    assert chat.turn_load == {"running": 0, "waiting": 0}


async def test_a_turn_waiting_for_a_slot_holds_no_connection(
    client: AsyncClient, org_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    first = uuid.UUID(await _conversation(client, org_headers))
    second = uuid.UUID(await _conversation(client, org_headers))
    monkeypatch.setattr(settings, "agent_max_concurrency", 1)
    monkeypatch.setattr(settings, "agent_fake_delay_ms", 400)
    maker = get_sessionmaker()
    async with maker() as a, maker() as b:
        one = asyncio.create_task(_drain(chat.run_message(a, conversation_id=first, text="hi")))
        await asyncio.sleep(0.05)
        two = asyncio.create_task(_drain(chat.run_message(b, conversation_id=second, text="hi")))
        await asyncio.sleep(0.15)
        assert chat.turn_load == {"running": 1, "waiting": 1}
        assert not b.in_transaction(), "a queued turn is holding a connection"
        done = await asyncio.gather(one, two)
    assert all(any(isinstance(e, TokenEvent) for e in events) for events in done)
    assert chat.turn_load == {"running": 0, "waiting": 0}


async def test_the_waiting_count_comes_back_down_when_the_wait_gives_up(
    client: AsyncClient, org_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    first = uuid.UUID(await _conversation(client, org_headers))
    second = uuid.UUID(await _conversation(client, org_headers))
    monkeypatch.setattr(settings, "agent_max_concurrency", 1)
    monkeypatch.setattr(settings, "agent_fake_delay_ms", 400)
    monkeypatch.setattr(settings, "agent_queue_wait_s", 0.05)
    maker = get_sessionmaker()
    async with maker() as a, maker() as b:
        one = asyncio.create_task(_drain(chat.run_message(a, conversation_id=first, text="hi")))
        await asyncio.sleep(0.05)
        refused = await _drain(chat.run_message(b, conversation_id=second, text="hi"))
        assert [e.code for e in refused if isinstance(e, ErrorEvent)] == ["busy"]
        assert chat.turn_load == {"running": 1, "waiting": 0}
        await one


async def test_an_open_chat_stream_keeps_no_connection_checked_out(
    client: AsyncClient, org_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The route's own session (who is asking, may they) is only closed
    when the response ends. For a stream that is the whole turn."""
    cid = await _conversation(client, org_headers)
    monkeypatch.setattr(settings, "agent_fake_delay_ms", 500)
    pool = get_engine().pool

    async def say() -> int:
        r = await client.post(
            f"{API}/conversations/{cid}/messages", json={"text": "hello"}, headers=org_headers
        )
        return r.status_code

    turn = asyncio.create_task(say())
    await asyncio.sleep(0.3)
    assert chat.turn_load["running"] == 1
    assert pool.checkedout() == 0  # type: ignore[attr-defined]
    assert await turn == 200


def test_the_pool_is_sized_from_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "database_url", "postgresql+asyncpg://u:p@localhost:1/x")
    monkeypatch.setattr(settings, "db_pool_size", 7)
    monkeypatch.setattr(settings, "db_max_overflow", 3)
    monkeypatch.setattr(settings, "db_pool_timeout_s", 2.5)
    monkeypatch.setattr(settings, "db_pool_recycle_s", 120)
    engine = db_session_module.get_engine.__wrapped__()  # nothing connects until used
    pool: Any = engine.pool
    assert (pool.size(), pool._max_overflow, pool._timeout, pool._recycle) == (7, 3, 2.5, 120)
    assert pool._pre_ping is True


async def test_the_process_gauges_are_on_the_page_and_never_cached(client: AsyncClient) -> None:
    first = (await client.get("/metrics")).text
    assert 'assistant_studio_turns{state="running"} 0' in first
    assert f"assistant_studio_turn_slots {settings.agent_max_concurrency}" in first
    chat.turn_load["waiting"] += 3
    try:
        # Within the cache's ten seconds, and still current.
        again = (await client.get("/metrics")).text
    finally:
        chat.turn_load["waiting"] -= 3
    assert 'assistant_studio_turns{state="waiting"} 3' in again


# ── the fake driver's turn length ────────────────────────────


async def test_a_fake_turn_takes_as_long_as_it_is_told_to(
    client: AsyncClient, org_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    cid = await _conversation(client, org_headers)
    fast = await loadtest.one_turn(client, org_headers, cid, "hello there")
    monkeypatch.setattr(settings, "agent_fake_delay_ms", 400)
    slow = await loadtest.one_turn(client, org_headers, cid, "hello there")
    assert fast.ok and slow.ok
    assert slow.total_s - fast.total_s > 0.3
    assert slow.total_s < 3


async def test_a_turn_that_ends_in_an_error_is_a_failure_with_its_code(
    client: AsyncClient, org_headers: dict[str, str]
) -> None:
    cid = await _conversation(client, org_headers)
    turn = await loadtest.one_turn(client, org_headers, cid, "fail: overloaded")
    assert not turn.ok
    assert turn.outcome not in ("ok", "no_done_event")


# ── the vector index ─────────────────────────────────────────


def test_a_search_asks_the_index_for_at_least_as_many_candidates_as_rows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "rag_hnsw_ef_search", 100)
    assert pgvector.ef_search_for(40) == 100
    assert pgvector.ef_search_for(250) == 250  # never fewer than the rows wanted
    assert pgvector.ef_search_for(5000) == pgvector.MAX_EF_SEARCH


async def test_the_index_settings_are_postgres_only(db_session: AsyncSession) -> None:
    # SQLite has no such settings: nothing is sent, nothing fails.
    await pgvector.tune_index_scan(db_session, 40)


POSTGRES_URL = os.environ.get(
    "INTEGRATION_DATABASE_URL", "postgresql+asyncpg://app:app@localhost:45432/app"
)


@pytest.mark.integration
async def test_the_index_settings_last_one_transaction(monkeypatch: pytest.MonkeyPatch) -> None:
    engine = create_async_engine(POSTGRES_URL)
    try:
        async with engine.connect():
            pass
    except Exception as exc:  # pragma: no cover - environment-dependent
        await engine.dispose()
        pytest.skip(f"Postgres not reachable: {type(exc).__name__}")
    monkeypatch.setattr(settings, "rag_hnsw_ef_search", 100)
    try:
        async with async_sessionmaker(engine)() as s:
            await pgvector.tune_index_scan(s, 250)
            assert await s.scalar(text("SHOW hnsw.ef_search")) == "250"
            assert await s.scalar(text("SHOW hnsw.iterative_scan")) == "relaxed_order"
            await s.commit()
            # The same connection, the next transaction: back to the defaults
            # (shown as empty until the extension is loaded in the session),
            # so nothing leaks to whoever gets this connection next.
            assert await s.scalar(text("SHOW hnsw.ef_search")) in ("", "40")
            assert await s.scalar(text("SHOW hnsw.iterative_scan")) in ("", "off")
    finally:
        await engine.dispose()


# ── the query cache ──────────────────────────────────────────


class _Embedder:
    def __init__(self, name: str = "stub") -> None:
        self.name = name
        self.calls: list[str] = []

    async def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [[0.0] for _ in texts]

    async def embed_query(self, text: str) -> list[float]:
        self.calls.append(text)
        return [float(len(self.calls)), float(len(text))]


async def test_the_same_question_is_embedded_once() -> None:
    e = _Embedder()
    first = await retrieve.query_embedding(e, "What is the refund policy?")
    again = await retrieve.query_embedding(e, "  What is   the refund policy? ")
    other = await retrieve.query_embedding(e, "what is the refund policy?")  # case matters
    assert again == first and other != first
    assert e.calls == ["What is the refund policy?", "what is the refund policy?"]
    assert metrics.QUERY_CACHE.value("hit") == 1
    assert metrics.QUERY_CACHE.value("miss") == 2


async def test_a_cached_vector_is_never_used_for_another_embedder() -> None:
    a, b = _Embedder("model-a"), _Embedder("model-b")
    await retrieve.query_embedding(a, "q")
    await retrieve.query_embedding(b, "q")
    assert (a.calls, b.calls) == (["q"], ["q"])


async def test_the_cache_is_bounded_and_expires(monkeypatch: pytest.MonkeyPatch) -> None:
    e = _Embedder()
    monkeypatch.setattr(settings, "rag_query_cache_size", 2)
    for q in ("a", "b", "a", "c"):  # "a" was used last, so "b" is the one to go
        await retrieve.query_embedding(e, q)
    assert e.calls == ["a", "b", "c"]
    await retrieve.query_embedding(e, "a")
    await retrieve.query_embedding(e, "b")
    assert e.calls == ["a", "b", "c", "b"]

    now = time.monotonic()
    monkeypatch.setattr(retrieve.time, "monotonic", lambda: now + 601)
    await retrieve.query_embedding(e, "b")
    assert e.calls[-1] == "b" and len(e.calls) == 5  # too old: embedded again


async def test_the_cache_can_be_switched_off(monkeypatch: pytest.MonkeyPatch) -> None:
    e = _Embedder()
    monkeypatch.setattr(settings, "rag_query_cache_size", 0)
    await retrieve.query_embedding(e, "q")
    await retrieve.query_embedding(e, "q")
    assert e.calls == ["q", "q"]
    # Off is off: nothing is looked up, so nothing is counted either.
    assert metrics.QUERY_CACHE.value("hit") == 0
    assert metrics.QUERY_CACHE.value("miss") == 0


# ── the load-test script ─────────────────────────────────────


def test_percentiles_are_nearest_rank() -> None:
    values = [float(n) for n in range(1, 101)]
    assert loadtest.percentiles(values) == {"p50": 50.5, "p95": 95.0, "p99": 99.0, "max": 100.0}
    assert loadtest.percentiles([2.0]) == {"p50": 2.0, "p95": 2.0, "p99": 2.0, "max": 2.0}
    assert loadtest.percentiles([]) is None


def test_the_gauges_are_read_out_of_a_metrics_page() -> None:
    page = (
        "# TYPE assistant_studio_turns gauge\n"
        'assistant_studio_turns{state="running"} 8\n'
        'assistant_studio_turns{state="waiting"} 24\n'
        'assistant_studio_db_pool_connections{state="idle"} 5\n'
        'assistant_studio_db_pool_connections{state="in_use"} 3\n'
        "assistant_studio_db_pool_limit 30\n"
        "assistant_studio_turn_slots 8\n"
        'assistant_studio_runs_total{status="ok"} 900\n'
    )
    assert loadtest.read_gauges(page) == {
        "turns_running": 8.0,
        "turns_waiting": 24.0,
        "db_connections_in_use": 3.0,
        "db_connections_limit": 30.0,
        "turn_slots": 8.0,
    }


def test_a_report_says_what_failed_and_whether_the_targets_were_met() -> None:
    report = loadtest.Report(concurrency=4)
    report.turns = [loadtest.Turn(True, 0.5, 2.0)] * 18 + [
        loadtest.Turn(True, 4.0, 13.0),
        loadtest.Turn(False, None, 30.0, "busy"),
        loadtest.Turn(False, None, 0.1, "http_429"),
    ]
    report.wall_s = 10.0
    summary = report.summary()
    assert (summary["ok"], summary["failed"], summary["turns_per_s"]) == (19, 2, 1.9)
    assert summary["outcomes"] == {"ok": 19, "busy": 1, "http_429": 1}
    shown = loadtest.show(summary)
    assert "OVER the 3.5 s target" in shown and "OVER the 12 s target" in shown
    assert "failures: busy x1, http_429 x1" in shown
    # Failed turns are not in the timings: a fast refusal is not a fast answer.
    assert summary["full_answer_s"]["max"] == 13.0


async def test_the_script_runs_a_whole_load_level(
    client: AsyncClient, org_headers: dict[str, str]
) -> None:
    a = (await client.post(f"{API}/assistants", json={"name": "Load"}, headers=org_headers)).json()
    report = await loadtest.run(
        client, org_headers, a["id"], concurrency=3, turns=7, message="hello"
    )
    summary = report.summary()
    assert (summary["turns"], summary["ok"], summary["failed"]) == (7, 7, 0)
    assert summary["first_token_s"]["p95"] <= summary["full_answer_s"]["p95"]
    assert summary["peaks"]["turn_slots"] == settings.agent_max_concurrency
