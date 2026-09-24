"""``retrieve()`` end to end — real Postgres, real local embedder + reranker
(cached from 2.4, so this is fast: no fresh download). Deselected by
default: ``pytest -m integration``. Pure RRF-fusion math is unit-tested
separately in test_rag_rrf.py, with no DB needed.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import AsyncIterator

import pytest
from app.models.assistant import Assistant, AssistantStatus
from app.models.organization import Organization
from app.models.rag import DataSource, DataSourceType, Document
from app.rag.embedders import get_embedder
from app.rag.retrieve import retrieve
from app.rag.vectorstore import ChunkRecord, PgVectorStore
from app.schemas.assistant_config import RagRetrieval
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

pytestmark = pytest.mark.integration

POSTGRES_URL = os.environ.get(
    "INTEGRATION_DATABASE_URL", "postgresql+asyncpg://app:app@localhost:45432/app"
)

_TEXTS = [
    "Refunds are processed within five business days of the original request.",
    "Our office is open Monday through Friday, nine in the morning until five in the evening.",
    "New employees receive their laptop and badge on their first day of onboarding.",
]


@pytest.fixture
async def pg() -> AsyncIterator[AsyncSession]:
    engine = create_async_engine(POSTGRES_URL)
    try:
        async with engine.connect():
            pass
    except Exception as exc:  # pragma: no cover - environment-dependent
        await engine.dispose()
        pytest.skip(f"Postgres not reachable at {POSTGRES_URL}: {exc}")

    sessionmaker = async_sessionmaker(engine, expire_on_commit=False)
    async with sessionmaker() as session:
        yield session
    await engine.dispose()


@pytest.fixture
async def seeded_assistant(pg: AsyncSession) -> AsyncIterator[Assistant]:
    org = Organization(name="Retrieve Test Org", slug=f"retrieve-test-{uuid.uuid4().hex[:8]}")
    pg.add(org)
    await pg.flush()
    assistant = Assistant(
        org_id=org.id, name="Retrieve Bot", slug="retrieve-bot", status=AssistantStatus.draft
    )
    pg.add(assistant)
    await pg.flush()

    source = DataSource(
        assistant_id=assistant.id, org_id=org.id, type=DataSourceType.text, name="Handbook"
    )
    pg.add(source)
    await pg.flush()
    document = Document(
        data_source_id=source.id, assistant_id=assistant.id, org_id=org.id, title="Handbook"
    )
    pg.add(document)
    await pg.flush()

    embedder = get_embedder()
    vectors = await embedder.embed_documents(_TEXTS)
    records = [
        ChunkRecord(
            document_id=document.id,
            assistant_id=assistant.id,
            org_id=org.id,
            ordinal=i,
            content=text,
            embedding=vector,
        )
        for i, (text, vector) in enumerate(zip(_TEXTS, vectors, strict=True))
    ]
    await PgVectorStore().upsert(pg, records)
    await pg.commit()

    try:
        yield assistant
    finally:
        fresh_org = await pg.get(Organization, org.id)
        if fresh_org is not None:
            await pg.delete(fresh_org)
            await pg.commit()


async def test_retrieve_surfaces_the_relevant_chunk_first(
    pg: AsyncSession, seeded_assistant: Assistant
) -> None:
    results = await retrieve(
        pg,
        assistant_id=seeded_assistant.id,
        query="how long do refunds take?",
        config=RagRetrieval(),
    )
    assert results
    assert "Refunds" in results[0].content
    assert results[0].title == "Handbook"
    assert results[0].data_source_id is not None


async def test_a_high_min_score_filters_out_weak_matches(
    pg: AsyncSession, seeded_assistant: Assistant
) -> None:
    strict_config = RagRetrieval(min_score=0.999)
    results = await retrieve(
        pg,
        assistant_id=seeded_assistant.id,
        query="how long do refunds take?",
        config=strict_config,
    )
    # A near-impossible bar to clear — nothing should survive it.
    assert results == []


async def test_retrieve_returns_nothing_for_a_different_assistant(
    pg: AsyncSession, seeded_assistant: Assistant
) -> None:
    results = await retrieve(
        pg, assistant_id=uuid.uuid4(), query="how long do refunds take?", config=RagRetrieval()
    )
    assert results == []
