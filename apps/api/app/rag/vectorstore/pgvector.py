"""pgvector-backed ``VectorStore`` — the v1 (and so far only) implementation.

Dense search is cosine distance over the ``chunks.embedding`` HNSW index
(migration ``4d19bec6d86b``). Scores are converted from distance (0 =
identical) to similarity (1 = identical) so callers of ``query`` always deal
in "higher is better." Sparse search is Postgres full-text over ``tsv``,
populated here (not by ingestion) — see ``upsert``.
"""

from __future__ import annotations

import uuid

from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.rag import Chunk, Document
from app.rag.vectorstore.base import ChunkRecord, ScoredChunk


class PgVectorStore:
    name = "pgvector"

    async def upsert(self, session: AsyncSession, chunks: list[ChunkRecord]) -> list[uuid.UUID]:
        rows = [
            Chunk(
                **({"id": c.id} if c.id is not None else {}),
                document_id=c.document_id,
                assistant_id=c.assistant_id,
                org_id=c.org_id,
                ordinal=c.ordinal,
                content=c.content,
                context_prefix=c.context_prefix,
                token_count=c.token_count,
                chunk_metadata=c.metadata,
                embedding=c.embedding,
            )
            for c in chunks
        ]
        session.add_all(rows)
        await session.flush()
        ids = [r.id for r in rows]

        # Postgres's concat() (unlike ||) treats NULL as empty string, so a
        # missing context_prefix doesn't null out the whole vector.
        tsv_expr = func.to_tsvector(
            "english", func.concat(Chunk.context_prefix, " ", Chunk.content)
        )
        await session.execute(update(Chunk).where(Chunk.id.in_(ids)).values(tsv=tsv_expr))
        return ids

    async def query(
        self,
        session: AsyncSession,
        *,
        assistant_id: uuid.UUID,
        embedding: list[float],
        top_k: int,
        source_ids: list[uuid.UUID] | None = None,
    ) -> list[ScoredChunk]:
        distance = Chunk.embedding.cosine_distance(embedding)
        stmt = (
            select(Chunk, distance.label("distance"))
            .where(Chunk.assistant_id == assistant_id)
            .order_by(distance)
            .limit(top_k)
        )
        if source_ids:
            stmt = stmt.join(Document, Document.id == Chunk.document_id).where(
                Document.data_source_id.in_(source_ids)
            )
        rows = (await session.execute(stmt)).all()
        return [
            ScoredChunk(
                id=chunk.id,
                document_id=chunk.document_id,
                content=chunk.content,
                score=1.0 - float(dist),
                metadata=chunk.chunk_metadata,
                ordinal=chunk.ordinal,
            )
            for chunk, dist in rows
        ]

    async def query_sparse(
        self,
        session: AsyncSession,
        *,
        assistant_id: uuid.UUID,
        query_text: str,
        top_k: int,
        source_ids: list[uuid.UUID] | None = None,
    ) -> list[ScoredChunk]:
        tsquery = func.websearch_to_tsquery("english", query_text)
        rank = func.ts_rank_cd(Chunk.tsv, tsquery)
        stmt = (
            select(Chunk, rank.label("rank"))
            .where(Chunk.assistant_id == assistant_id, Chunk.tsv.op("@@")(tsquery))
            .order_by(rank.desc())
            .limit(top_k)
        )
        if source_ids:
            stmt = stmt.join(Document, Document.id == Chunk.document_id).where(
                Document.data_source_id.in_(source_ids)
            )
        rows = (await session.execute(stmt)).all()
        return [
            ScoredChunk(
                id=chunk.id,
                document_id=chunk.document_id,
                content=chunk.content,
                score=float(rank_value),
                metadata=chunk.chunk_metadata,
                ordinal=chunk.ordinal,
            )
            for chunk, rank_value in rows
        ]

    async def delete_by_document(self, session: AsyncSession, document_id: uuid.UUID) -> None:
        await session.execute(delete(Chunk).where(Chunk.document_id == document_id))


__all__ = ["PgVectorStore"]
