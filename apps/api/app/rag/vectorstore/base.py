"""``VectorStore``: the swap point for the chunk index (pgvector today, maybe
Qdrant later — see ADR 0001's tech-stack table). Everything above this
interface (ingestion, retrieval) is written against these three operations
only, never against pgvector-specific SQL directly.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any, Protocol

from sqlalchemy.ext.asyncio import AsyncSession


@dataclass
class ChunkRecord:
    """One chunk ready to be indexed — everything ``upsert`` needs to know."""

    document_id: uuid.UUID
    assistant_id: uuid.UUID
    org_id: uuid.UUID
    ordinal: int
    content: str
    embedding: list[float]
    token_count: int = 0
    context_prefix: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    #: A stable id (ingestion derives it from document, ordinal and content);
    #: None lets the database generate one.
    id: uuid.UUID | None = None


@dataclass
class ScoredChunk:
    """One retrieval hit — a chunk plus how it scored for a query."""

    id: uuid.UUID
    document_id: uuid.UUID
    content: str
    score: float
    metadata: dict[str, Any] = field(default_factory=dict)
    ordinal: int | None = None


class VectorStore(Protocol):
    async def upsert(self, session: AsyncSession, chunks: list[ChunkRecord]) -> list[uuid.UUID]:
        """Insert chunks (with their embeddings) and return their new ids."""
        ...

    async def query(
        self,
        session: AsyncSession,
        *,
        assistant_id: uuid.UUID,
        embedding: list[float],
        top_k: int,
        source_ids: list[uuid.UUID] | None = None,
    ) -> list[ScoredChunk]:
        """Dense kNN search (cosine distance) scoped to one assistant's chunks,
        optionally narrowed to specific data sources."""
        ...

    async def query_sparse(
        self,
        session: AsyncSession,
        *,
        assistant_id: uuid.UUID,
        query_text: str,
        top_k: int,
        source_ids: list[uuid.UUID] | None = None,
    ) -> list[ScoredChunk]:
        """Full-text search (Postgres ``tsvector``/``websearch_to_tsquery``) —
        the sparse half of hybrid retrieval. Only returns chunks that
        actually match; unlike dense search there's no "closest anyway"
        fallback, so this can return fewer than ``top_k`` results, including
        zero."""
        ...

    async def delete_by_document(self, session: AsyncSession, document_id: uuid.UUID) -> None: ...


__all__ = ["ChunkRecord", "ScoredChunk", "VectorStore"]
