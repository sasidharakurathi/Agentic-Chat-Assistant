"""``kb_search`` / ``kb_list_sources`` handlers against a real seeded
knowledge base — real Postgres + real local embedder/reranker (cached from
2.4). Deselected by default: ``pytest -m integration``.

The handlers deliberately open their *own* DB session via the app's
``get_sessionmaker()`` (same shape as ``app/worker.py``'s job function) —
not one passed in by the caller, since a tool call mid-turn isn't the outer
request's session's business. That means testing them for real requires the
app's session factory to actually point at the same Postgres this test
seeds into, not the SQLite DB the rest of the suite runs against. The
autouse fixture below does exactly that, for this file only.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import AsyncIterator

import pytest
from app.agent.caps_rag import build_kb_list_sources_tool, build_kb_search_tool
from app.config import settings
from app.db import session as db_session
from app.models.assistant import Assistant, AssistantStatus
from app.models.organization import Organization
from app.models.rag import DataSource, DataSourceStatus, DataSourceType, Document
from app.rag.embedders import get_embedder
from app.rag.vectorstore import ChunkRecord, PgVectorStore
from app.schemas.assistant_config import RagConfig
from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = pytest.mark.integration

POSTGRES_URL = os.environ.get(
    "INTEGRATION_DATABASE_URL", "postgresql+asyncpg://app:app@localhost:45432/app"
)


@pytest.fixture(autouse=True)
async def _point_app_db_at_postgres(monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[None]:
    monkeypatch.setattr(settings, "database_url", POSTGRES_URL)
    db_session.get_engine.cache_clear()
    db_session.get_sessionmaker.cache_clear()
    try:
        async with db_session.get_engine().connect():
            pass
    except Exception as exc:  # pragma: no cover - environment-dependent
        db_session.get_engine.cache_clear()
        db_session.get_sessionmaker.cache_clear()
        pytest.skip(f"Postgres not reachable at {POSTGRES_URL}: {exc}")
    yield
    db_session.get_engine.cache_clear()
    db_session.get_sessionmaker.cache_clear()


@pytest.fixture
async def pg() -> AsyncIterator[AsyncSession]:
    async with db_session.get_sessionmaker()() as session:
        yield session


@pytest.fixture
async def seeded_assistant(pg: AsyncSession) -> AsyncIterator[Assistant]:
    org = Organization(name="Caps RAG Test Org", slug=f"caps-rag-test-{uuid.uuid4().hex[:8]}")
    pg.add(org)
    await pg.flush()
    assistant = Assistant(
        org_id=org.id, name="Caps RAG Bot", slug="caps-rag-bot", status=AssistantStatus.draft
    )
    pg.add(assistant)
    await pg.flush()

    source = DataSource(
        assistant_id=assistant.id,
        org_id=org.id,
        type=DataSourceType.text,
        name="Handbook",
        status=DataSourceStatus.ready,
    )
    pg.add(source)
    await pg.flush()
    document = Document(
        data_source_id=source.id, assistant_id=assistant.id, org_id=org.id, title="Handbook"
    )
    pg.add(document)
    await pg.flush()

    text = "Refunds are processed within five business days of the original request."
    embedder = get_embedder()
    [vector] = await embedder.embed_documents([text])
    await PgVectorStore().upsert(
        pg,
        [
            ChunkRecord(
                document_id=document.id,
                assistant_id=assistant.id,
                org_id=org.id,
                ordinal=0,
                content=text,
                embedding=vector,
            )
        ],
    )
    await pg.commit()

    try:
        yield assistant
    finally:
        fresh_org = await pg.get(Organization, org.id)
        if fresh_org is not None:
            await pg.delete(fresh_org)
            await pg.commit()


async def test_kb_search_handler_returns_cited_results(seeded_assistant: Assistant) -> None:
    tool = build_kb_search_tool(seeded_assistant.id, RagConfig(enabled=True))
    result = await tool.handler({"query": "how long do refunds take?"})
    text = result["content"][0]["text"]
    assert "[1]" in text
    assert "Refunds" in text
    assert "Handbook" in text


async def test_kb_search_handler_reports_no_results_for_an_empty_kb() -> None:
    tool = build_kb_search_tool(uuid.uuid4(), RagConfig(enabled=True))
    result = await tool.handler({"query": "anything"})
    text = result["content"][0]["text"]
    assert "No relevant results" in text


async def test_kb_list_sources_handler_lists_ready_sources(seeded_assistant: Assistant) -> None:
    tool = build_kb_list_sources_tool(seeded_assistant.id)
    result = await tool.handler({})
    text = result["content"][0]["text"]
    assert "Handbook" in text


async def test_kb_list_sources_handler_reports_none_for_an_empty_kb() -> None:
    tool = build_kb_list_sources_tool(uuid.uuid4())
    result = await tool.handler({})
    text = result["content"][0]["text"]
    assert "No indexed" in text


# ── source scoping, uri/score, counts (task 2.8) ───────────────────


async def _source_id(pg: AsyncSession, assistant: Assistant) -> uuid.UUID:
    from sqlalchemy import select

    sid = await pg.scalar(select(DataSource.id).where(DataSource.assistant_id == assistant.id))
    assert sid is not None
    return sid


async def test_kb_search_results_carry_score_and_source(
    pg: AsyncSession, seeded_assistant: Assistant
) -> None:
    sid = await _source_id(pg, seeded_assistant)
    tool = build_kb_search_tool(seeded_assistant.id, RagConfig(enabled=True))
    text = (await tool.handler({"query": "how long do refunds take?"}))["content"][0]["text"]
    assert f"source_id={sid}" in text
    assert "score=" in text


async def test_kb_search_can_be_scoped_to_listed_sources(
    pg: AsyncSession, seeded_assistant: Assistant
) -> None:
    sid = await _source_id(pg, seeded_assistant)
    tool = build_kb_search_tool(seeded_assistant.id, RagConfig(enabled=True))
    hit = await tool.handler({"query": "refunds", "source_ids": [str(sid)]})
    assert "Refunds" in hit["content"][0]["text"]
    miss = await tool.handler({"query": "refunds", "source_ids": [str(uuid.uuid4())]})
    assert "No relevant results" in miss["content"][0]["text"]


async def test_kb_search_cannot_widen_past_the_assistants_sources(
    pg: AsyncSession, seeded_assistant: Assistant
) -> None:
    sid = await _source_id(pg, seeded_assistant)
    only_other = RagConfig(enabled=True, source_ids=[str(uuid.uuid4())])
    tool = build_kb_search_tool(seeded_assistant.id, only_other)
    result = await tool.handler({"query": "refunds", "source_ids": [str(sid)]})
    assert result.get("is_error") is True
    assert "none of those sources" in result["content"][0]["text"]


async def test_kb_search_rejects_malformed_source_ids(seeded_assistant: Assistant) -> None:
    tool = build_kb_search_tool(seeded_assistant.id, RagConfig(enabled=True))
    for bad in (["not-a-uuid"], "abc"):
        result = await tool.handler({"query": "refunds", "source_ids": bad})
        assert result.get("is_error") is True


async def test_kb_list_sources_shows_counts_and_respects_the_allow_list(
    pg: AsyncSession, seeded_assistant: Assistant
) -> None:
    sid = await _source_id(pg, seeded_assistant)
    text = (await build_kb_list_sources_tool(seeded_assistant.id).handler({}))["content"][0]["text"]
    assert f"id={sid}" in text and "1 doc, 1 chunk" in text
    hidden = await build_kb_list_sources_tool(seeded_assistant.id, [str(uuid.uuid4())]).handler({})
    assert "No indexed" in hidden["content"][0]["text"]


# ── chunks loaded again by id, for a citation reused from an earlier turn ──


async def test_chunks_by_id_loads_a_chunk_with_its_source(
    seeded_assistant: Assistant, pg: AsyncSession
) -> None:
    from app.models.rag import Chunk
    from app.rag.retrieve import chunks_by_id
    from sqlalchemy import select

    chunk_id = (
        await pg.execute(select(Chunk.id).where(Chunk.assistant_id == seeded_assistant.id))
    ).scalar_one()
    (found,) = await chunks_by_id(
        pg, assistant_id=seeded_assistant.id, chunk_ids=[str(chunk_id), "not-a-uuid"]
    )
    assert found.chunk_id == chunk_id
    assert found.title == "Handbook"
    assert found.source_type == "text"
    assert found.data_source_id is not None
    assert "five business days" in found.content


async def test_chunks_by_id_never_returns_another_assistants_chunk(
    seeded_assistant: Assistant, pg: AsyncSession
) -> None:
    from app.models.rag import Chunk
    from app.rag.retrieve import chunks_by_id
    from sqlalchemy import select

    chunk_id = (
        await pg.execute(select(Chunk.id).where(Chunk.assistant_id == seeded_assistant.id))
    ).scalar_one()
    assert await chunks_by_id(pg, assistant_id=uuid.uuid4(), chunk_ids=[str(chunk_id)]) == []
