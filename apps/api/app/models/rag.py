"""Knowledge base domain model: data_sources -> documents -> chunks.

A data source is what the user adds (a file, a URL, pasted text). Ingestion
(task 2.5) parses it into one or more documents, then splits each document
into chunks — the actual retrieval unit, each carrying its own embedding and
full-text vector.
"""

from __future__ import annotations

import enum
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import Enum as SAEnum
from sqlalchemy import ForeignKey, Index, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, UUIDPrimaryKeyMixin
from app.db.types import JSONB, TSV, Embedding, TZDateTime
from app.rag import EMBEDDING_DIM


class DataSourceType(enum.StrEnum):
    file = "file"
    url = "url"
    text = "text"


class DataSourceStatus(enum.StrEnum):
    pending = "pending"
    processing = "processing"
    ready = "ready"
    error = "error"


class DataSource(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "data_sources"

    assistant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("assistants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    org_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    type: Mapped[DataSourceType] = mapped_column(
        SAEnum(DataSourceType, name="data_source_type", native_enum=False, length=20),
        nullable=False,
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    # file: MinIO object key (below). url: the source URL. text: unused (raw
    # text is stored on the one Document this source produces).
    uri: Mapped[str | None] = mapped_column(Text)
    object_key: Mapped[str | None] = mapped_column(String(500))
    bytes: Mapped[int | None] = mapped_column(Integer)
    checksum: Mapped[str | None] = mapped_column(String(128))
    status: Mapped[DataSourceStatus] = mapped_column(
        SAEnum(DataSourceStatus, name="data_source_status", native_enum=False, length=20),
        nullable=False,
        default=DataSourceStatus.pending,
    )
    error: Mapped[str | None] = mapped_column(Text)
    config: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        TZDateTime(), server_default=func.now(), nullable=False
    )
    indexed_at: Mapped[datetime | None] = mapped_column(TZDateTime())
    #: What the last ingestion did: chunks, reuse, context coverage, cost.
    #: See `app.rag.ingest.IngestReport`.
    ingest_report: Mapped[dict[str, Any] | None] = mapped_column(JSONB)


class Document(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "documents"

    data_source_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("data_sources.id", ondelete="CASCADE"), nullable=False, index=True
    )
    assistant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("assistants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    org_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    source_uri: Mapped[str | None] = mapped_column(Text)
    mime: Mapped[str | None] = mapped_column(String(120))
    page_count: Mapped[int | None] = mapped_column(Integer)
    token_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    checksum: Mapped[str | None] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(
        TZDateTime(), server_default=func.now(), nullable=False
    )


def _postgres_only(index: Index) -> Index:
    """An index that exists only on Postgres (F-7).

    The HNSW and GIN indexes need pgvector / tsvector, which SQLite (the
    offline unit tier) does not have. They were created with raw SQL in the
    migration and never declared here, so the models and the database
    disagreed: `alembic check` failed, and the next autogenerate would have
    written `drop_index` for both — silently turning every retrieval into a
    sequential scan. Declaring them makes the model the single source of
    truth again. `ddl_if` keeps `create_all` from building them on SQLite;
    the `info` tag lets `migrations/env.py` skip them when comparing there.
    """
    index.info["only_on"] = "postgresql"
    return index.ddl_if(dialect="postgresql")


class Chunk(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "chunks"
    __table_args__ = (
        _postgres_only(
            Index(
                "ix_chunks_embedding_hnsw",
                "embedding",
                postgresql_using="hnsw",
                postgresql_ops={"embedding": "vector_cosine_ops"},
            )
        ),
        _postgres_only(Index("ix_chunks_tsv_gin", "tsv", postgresql_using="gin")),
    )

    document_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), nullable=False, index=True
    )
    assistant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("assistants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    org_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    # Contextual-retrieval prefix (task 2.6) — a short LLM-generated blurb
    # situating this chunk within its document, prepended before embedding.
    context_prefix: Mapped[str | None] = mapped_column(Text)
    token_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    chunk_metadata: Mapped[dict[str, Any]] = mapped_column(
        "metadata", JSONB, nullable=False, default=dict
    )
    embedding: Mapped[list[float] | None] = mapped_column(Embedding(EMBEDDING_DIM))
    tsv: Mapped[str | None] = mapped_column(TSV)
    created_at: Mapped[datetime] = mapped_column(
        TZDateTime(), server_default=func.now(), nullable=False
    )


__all__ = [
    "Chunk",
    "DataSource",
    "DataSourceStatus",
    "DataSourceType",
    "Document",
]
