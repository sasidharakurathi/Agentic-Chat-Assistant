"""Offline (SQLite) round-trip for the knowledge-base models — basic CRUD
only. Real similarity search is Postgres-only; see
tests/test_rag_vectorstore.py (``-m integration``)."""

from __future__ import annotations

import uuid

from app.models.assistant import Assistant, AssistantStatus
from app.models.organization import Organization
from app.models.rag import Chunk, DataSource, DataSourceStatus, DataSourceType, Document
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession


async def _seed(session: AsyncSession) -> tuple[Organization, Assistant]:
    org = Organization(name="KB Org", slug=f"kb-org-{uuid.uuid4().hex[:8]}")
    session.add(org)
    await session.flush()
    assistant = Assistant(org_id=org.id, name="KB Bot", slug="kb-bot", status=AssistantStatus.draft)
    session.add(assistant)
    await session.flush()
    return org, assistant


async def test_data_source_document_chunk_round_trip(db_session: AsyncSession) -> None:
    org, assistant = await _seed(db_session)

    source = DataSource(
        assistant_id=assistant.id,
        org_id=org.id,
        type=DataSourceType.file,
        name="handbook.pdf",
        object_key="uploads/handbook.pdf",
        status=DataSourceStatus.pending,
    )
    db_session.add(source)
    await db_session.flush()

    document = Document(
        data_source_id=source.id,
        assistant_id=assistant.id,
        org_id=org.id,
        title="Employee Handbook",
        mime="application/pdf",
        page_count=12,
    )
    db_session.add(document)
    await db_session.flush()

    chunk = Chunk(
        document_id=document.id,
        assistant_id=assistant.id,
        org_id=org.id,
        ordinal=0,
        content="All employees get unlimited coffee.",
        token_count=8,
        chunk_metadata={"page": 3},
        embedding=[0.1, 0.2, 0.3],
    )
    db_session.add(chunk)
    await db_session.commit()

    got = await db_session.scalar(select(Chunk).where(Chunk.id == chunk.id))
    assert got is not None
    assert got.content == "All employees get unlimited coffee."
    assert got.chunk_metadata == {"page": 3}
    assert got.embedding == [0.1, 0.2, 0.3]
    assert got.document_id == document.id
