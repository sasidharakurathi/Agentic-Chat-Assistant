"""``ingest_data_source`` end to end — needs real Postgres (chunks/pgvector)
and the real local embedder (``RAG_OFFLINE=1`` in ``.env``, cached from
task 2.4's run, so this is fast: no fresh multi-GB download). Deselected by
default: ``pytest -m integration``.

Unlike test_rag_vectorstore.py, this can't use rollback-only isolation —
``ingest_data_source`` commits internally (status transitions must survive
even if a later step fails), so each test explicitly deletes what it created.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import AsyncIterator

import pytest
from app.models.assistant import Assistant, AssistantStatus
from app.models.organization import Organization
from app.models.rag import Chunk, DataSource, DataSourceStatus, DataSourceType, Document
from app.models.usage import UsageEvent as UsageRow
from app.models.usage import UsageKind
from app.rag.ingest import ingest_data_source
from app.storage import put_object
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

pytestmark = pytest.mark.integration

POSTGRES_URL = os.environ.get(
    "INTEGRATION_DATABASE_URL", "postgresql+asyncpg://app:app@localhost:45432/app"
)


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
async def assistant(pg: AsyncSession) -> AsyncIterator[Assistant]:
    org = Organization(name="Ingest Test Org", slug=f"ingest-test-{uuid.uuid4().hex[:8]}")
    pg.add(org)
    await pg.flush()
    a = Assistant(org_id=org.id, name="Ingest Bot", slug="ingest-bot", status=AssistantStatus.draft)
    pg.add(a)
    await pg.commit()
    # Captured now: ingestion shares this session, and a failed ingest rolls
    # it back, which expires every loaded object (a later `org.id` would be a
    # lazy load outside the async context).
    org_id = org.id
    try:
        yield a
    finally:
        # Cascades: assistant -> data_sources -> documents -> chunks, and
        # org -> assistant, all via ondelete=CASCADE.
        fresh_org = await pg.get(Organization, org_id)
        if fresh_org is not None:
            await pg.delete(fresh_org)
            await pg.commit()


async def test_text_source_ingests_into_a_document_and_chunks(
    pg: AsyncSession, assistant: Assistant
) -> None:
    source = DataSource(
        assistant_id=assistant.id,
        org_id=assistant.org_id,
        type=DataSourceType.text,
        name="fixture",
        status=DataSourceStatus.pending,
        config={"text": "Refunds are processed within 5 business days of the request."},
    )
    pg.add(source)
    await pg.commit()

    await ingest_data_source(pg, source.id)

    await pg.refresh(source)
    assert source.status == DataSourceStatus.ready
    assert source.indexed_at is not None

    document = await pg.scalar(select(Document).where(Document.data_source_id == source.id))
    assert document is not None
    assert document.token_count > 0

    chunks = list(await pg.scalars(select(Chunk).where(Chunk.document_id == document.id)))
    assert len(chunks) >= 1
    assert chunks[0].embedding is not None
    assert len(chunks[0].embedding) == 1024
    assert "Refunds" in chunks[0].content


async def test_file_source_fetches_from_minio_and_ingests(
    pg: AsyncSession, assistant: Assistant
) -> None:
    key = f"data-sources/{assistant.id}/test-{uuid.uuid4().hex[:8]}/notes.txt"
    await put_object(key, b"The office is open Monday through Friday, 9am to 5pm.", "text/plain")

    source = DataSource(
        assistant_id=assistant.id,
        org_id=assistant.org_id,
        type=DataSourceType.file,
        name="notes.txt",
        object_key=key,
        status=DataSourceStatus.pending,
        config={"content_type": "text/plain"},
    )
    pg.add(source)
    await pg.commit()

    await ingest_data_source(pg, source.id)

    await pg.refresh(source)
    assert source.status == DataSourceStatus.ready
    chunks = list(
        await pg.scalars(
            select(Chunk)
            .join(Document, Document.id == Chunk.document_id)
            .where(Document.data_source_id == source.id)
        )
    )
    assert any("office" in c.content.lower() for c in chunks)


async def test_reindexing_replaces_chunks_instead_of_duplicating(
    pg: AsyncSession, assistant: Assistant
) -> None:
    source = DataSource(
        assistant_id=assistant.id,
        org_id=assistant.org_id,
        type=DataSourceType.text,
        name="fixture",
        status=DataSourceStatus.pending,
        config={"text": "Version one of the content."},
    )
    pg.add(source)
    await pg.commit()

    await ingest_data_source(pg, source.id)
    first_count = len(
        list(
            await pg.scalars(
                select(Chunk)
                .join(Document, Document.id == Chunk.document_id)
                .where(Document.data_source_id == source.id)
            )
        )
    )

    # Reindex with different content — a real edit-and-reindex scenario.
    source.config = {"text": "Version two of the content, which is quite a bit longer now."}
    await pg.commit()
    await ingest_data_source(pg, source.id)

    docs = list(await pg.scalars(select(Document).where(Document.data_source_id == source.id)))
    assert len(docs) == 1  # old document was replaced, not accumulated
    chunks = list(await pg.scalars(select(Chunk).where(Chunk.document_id == docs[0].id)))
    assert len(chunks) >= 1
    assert all("Version two" in c.content or "longer" in c.content for c in chunks)
    assert first_count >= 1  # sanity: the first ingest actually produced something


async def test_failed_ingestion_marks_source_as_error(
    pg: AsyncSession, assistant: Assistant
) -> None:
    source = DataSource(
        assistant_id=assistant.id,
        org_id=assistant.org_id,
        type=DataSourceType.text,
        name="empty",
        status=DataSourceStatus.pending,
        config={"text": "   "},  # blank -> IngestError("text data source has no content")
    )
    pg.add(source)
    await pg.commit()

    with pytest.raises(Exception, match="no content"):
        await ingest_data_source(pg, source.id)

    await pg.refresh(source)
    assert source.status == DataSourceStatus.error
    assert source.error is not None and "no content" in source.error


# ── usage accounting (task 1.8 / 2.6) ────────────────────────


async def _text_source(pg: AsyncSession, assistant: Assistant) -> DataSource:
    source = DataSource(
        assistant_id=assistant.id,
        org_id=assistant.org_id,
        type=DataSourceType.text,
        name="usage fixture",
        status=DataSourceStatus.pending,
        config={"text": "Warranty claims must be filed within ninety days of purchase."},
    )
    pg.add(source)
    await pg.commit()
    return source


async def _usage_rows(pg: AsyncSession, assistant_id: uuid.UUID) -> list[UsageRow]:
    rows = await pg.scalars(select(UsageRow).where(UsageRow.assistant_id == assistant_id))
    return list(rows.all())


async def test_ingestion_records_its_embedding_usage(
    pg: AsyncSession, assistant: Assistant
) -> None:
    assistant_id = assistant.id
    source = await _text_source(pg, assistant)
    await ingest_data_source(pg, source.id)
    rows = await _usage_rows(pg, assistant_id)
    (embed,) = [r for r in rows if r.kind is UsageKind.embedding]
    assert embed.model == "local-bge-m3" and embed.tokens_in > 0
    assert float(embed.cost_usd) == 0, "the local model is free, but still metered"


async def test_a_failed_ingest_still_records_what_it_spent(
    pg: AsyncSession, assistant: Assistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The embedding call happened (and on Voyage, was billed) before the
    write failed. It used to be rolled back along with everything else."""

    async def broken(*_a: object, **_k: object) -> None:
        raise RuntimeError("disk full")

    monkeypatch.setattr("app.rag.ingest.PgVectorStore.upsert", broken)
    assistant_id = assistant.id
    source = await _text_source(pg, assistant)
    source_id = source.id
    with pytest.raises(RuntimeError):
        await ingest_data_source(pg, source_id)
    fresh = await pg.get(DataSource, source_id)
    assert fresh is not None and fresh.status is DataSourceStatus.error
    assert [r.kind for r in await _usage_rows(pg, assistant_id)] == [UsageKind.embedding]
