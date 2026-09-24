"""``PgVectorStore`` against a *real* Postgres — cosine kNN and the HNSW
index only exist there, so there's no meaningful way to test this on SQLite
(see app/db/types.py). Deselected by default: run with ``pytest -m integration``
against the dev docker-compose stack (``docker compose up -d postgres``).

Each test runs inside a transaction that's rolled back at the end, so this
never leaves rows behind in the shared dev database.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import AsyncIterator

import pytest
from app.models.assistant import Assistant, AssistantStatus
from app.models.organization import Organization
from app.models.rag import Chunk, DataSource, DataSourceType, Document
from app.rag.vectorstore import ChunkRecord, PgVectorStore
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

pytestmark = pytest.mark.integration

POSTGRES_URL = os.environ.get(
    "INTEGRATION_DATABASE_URL", "postgresql+asyncpg://app:app@localhost:45432/app"
)


@pytest.fixture
async def pg(request: pytest.FixtureRequest) -> AsyncIterator[AsyncSession]:
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
        await session.rollback()
    await engine.dispose()


async def _seed_document(session: AsyncSession) -> Document:
    org = Organization(name="RAG Test Org", slug=f"rag-test-{uuid.uuid4().hex[:8]}")
    session.add(org)
    await session.flush()

    assistant = Assistant(
        org_id=org.id, name="RAG Test Assistant", slug="rag-test", status=AssistantStatus.draft
    )
    session.add(assistant)
    await session.flush()

    source = DataSource(
        assistant_id=assistant.id, org_id=org.id, type=DataSourceType.text, name="fixture"
    )
    session.add(source)
    await session.flush()

    document = Document(
        data_source_id=source.id, assistant_id=assistant.id, org_id=org.id, title="fixture doc"
    )
    session.add(document)
    await session.flush()
    return document


def _vec(*, lead: float) -> list[float]:
    """A cheap, deterministic 1024-d vector: one distinguishing lead value,
    the rest constant — enough to give cosine similarity a clear ranking
    without needing a real embedding model."""
    return [lead] + [0.01] * 1023


async def test_upsert_and_query_ranks_by_cosine_similarity(pg: AsyncSession) -> None:
    document = await _seed_document(pg)
    store = PgVectorStore()

    ids = await store.upsert(
        pg,
        [
            ChunkRecord(
                document_id=document.id,
                assistant_id=document.assistant_id,
                org_id=document.org_id,
                ordinal=0,
                content="close match",
                embedding=_vec(lead=1.0),
            ),
            ChunkRecord(
                document_id=document.id,
                assistant_id=document.assistant_id,
                org_id=document.org_id,
                ordinal=1,
                content="far match",
                embedding=_vec(lead=-1.0),
            ),
        ],
    )
    assert len(ids) == 2

    results = await store.query(
        pg, assistant_id=document.assistant_id, embedding=_vec(lead=1.0), top_k=2
    )
    assert [r.content for r in results] == ["close match", "far match"]
    assert results[0].score > results[1].score


async def test_query_scopes_to_assistant(pg: AsyncSession) -> None:
    doc_a = await _seed_document(pg)
    doc_b = await _seed_document(pg)
    store = PgVectorStore()

    await store.upsert(
        pg,
        [
            ChunkRecord(
                document_id=doc_a.id,
                assistant_id=doc_a.assistant_id,
                org_id=doc_a.org_id,
                ordinal=0,
                content="belongs to assistant a",
                embedding=_vec(lead=1.0),
            ),
            ChunkRecord(
                document_id=doc_b.id,
                assistant_id=doc_b.assistant_id,
                org_id=doc_b.org_id,
                ordinal=0,
                content="belongs to assistant b",
                embedding=_vec(lead=1.0),
            ),
        ],
    )

    results = await store.query(
        pg, assistant_id=doc_a.assistant_id, embedding=_vec(lead=1.0), top_k=10
    )
    assert [r.content for r in results] == ["belongs to assistant a"]


async def test_delete_by_document_removes_its_chunks(pg: AsyncSession) -> None:
    document = await _seed_document(pg)
    store = PgVectorStore()
    await store.upsert(
        pg,
        [
            ChunkRecord(
                document_id=document.id,
                assistant_id=document.assistant_id,
                org_id=document.org_id,
                ordinal=0,
                content="to be deleted",
                embedding=_vec(lead=1.0),
            )
        ],
    )

    await store.delete_by_document(pg, document.id)

    results = await store.query(
        pg, assistant_id=document.assistant_id, embedding=_vec(lead=1.0), top_k=10
    )
    assert results == []


async def test_deleting_data_source_cascades_through_document_to_chunks(pg: AsyncSession) -> None:
    """Real FK ondelete=CASCADE, only verifiable against Postgres — SQLite
    doesn't enforce it without a PRAGMA this app doesn't set (see
    test_rag_models.py)."""
    document = await _seed_document(pg)
    store = PgVectorStore()
    await store.upsert(
        pg,
        [
            ChunkRecord(
                document_id=document.id,
                assistant_id=document.assistant_id,
                org_id=document.org_id,
                ordinal=0,
                content="orphaned by cascade",
                embedding=_vec(lead=1.0),
            )
        ],
    )
    await pg.flush()

    source = await pg.get(DataSource, document.data_source_id)
    assert source is not None
    await pg.delete(source)
    await pg.flush()

    assert await pg.scalar(select(Document).where(Document.id == document.id)) is None
    assert await pg.scalar(select(Chunk).where(Chunk.document_id == document.id)) is None


async def test_upsert_populates_tsv_and_query_sparse_finds_it(pg: AsyncSession) -> None:
    document = await _seed_document(pg)
    store = PgVectorStore()
    await store.upsert(
        pg,
        [
            ChunkRecord(
                document_id=document.id,
                assistant_id=document.assistant_id,
                org_id=document.org_id,
                ordinal=0,
                content="Refunds are processed within five business days.",
                embedding=_vec(lead=1.0),
            ),
            ChunkRecord(
                document_id=document.id,
                assistant_id=document.assistant_id,
                org_id=document.org_id,
                ordinal=1,
                content="Our office hours are nine to five on weekdays.",
                embedding=_vec(lead=-1.0),
            ),
        ],
    )

    results = await store.query_sparse(
        pg, assistant_id=document.assistant_id, query_text="refunds", top_k=10
    )
    assert len(results) == 1
    assert "Refunds" in results[0].content


async def test_query_sparse_returns_nothing_for_a_non_matching_query(pg: AsyncSession) -> None:
    document = await _seed_document(pg)
    store = PgVectorStore()
    await store.upsert(
        pg,
        [
            ChunkRecord(
                document_id=document.id,
                assistant_id=document.assistant_id,
                org_id=document.org_id,
                ordinal=0,
                content="Refunds are processed within five business days.",
                embedding=_vec(lead=1.0),
            )
        ],
    )

    results = await store.query_sparse(
        pg, assistant_id=document.assistant_id, query_text="astrophysics", top_k=10
    )
    assert results == []
