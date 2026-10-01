"""pgvector-backed ``VectorStore`` — the v1 (and so far only) implementation.

Dense search is cosine distance over the ``chunks.embedding`` HNSW index
(migration ``4d19bec6d86b``). Scores are converted from distance (0 =
identical) to similarity (1 = identical) so callers of ``query`` always deal
in "higher is better." Sparse search is Postgres full-text over ``tsv``,
populated here (not by ingestion) — see ``upsert``.
"""

from __future__ import annotations

import uuid

from sqlalchemy import delete, func, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.rag import Chunk, Document
from app.rag.vectorstore.base import ChunkRecord, ScoredChunk

#: pgvector's ceiling for `hnsw.ef_search`.
MAX_EF_SEARCH = 1000


def ef_search_for(top_k: int) -> int:
    """The candidate list for one search: the configured size, and never
    smaller than the number of rows asked for.

    An HNSW scan returns at most `ef_search` rows. At pgvector's default of
    40, an assistant configured with `top_k_dense: 100` got 40 candidates
    and nothing said so (measured, task 6.5).
    """
    return min(MAX_EF_SEARCH, max(settings.rag_hnsw_ef_search, top_k))


async def tune_index_scan(session: AsyncSession, top_k: int) -> None:
    """Set the index's search parameters for this transaction only.

    - `hnsw.ef_search`: see `ef_search_for`.
    - `hnsw.iterative_scan`: the index is shared by every assistant, and
      the `WHERE assistant_id = ...` filter is applied to what the index
      returns. Without this, a scan that found `ef_search` neighbours, most
      of them another assistant's, returned the few that were left. With
      it the scan keeps going until it has `top_k` rows that pass the
      filter. `relaxed_order` may hand them back slightly out of order, so
      `query` sorts them again.

    Postgres only; the settings belong to the pgvector extension (0.8+).
    `set_config(..., true)` is `SET LOCAL`: it ends with the transaction and
    never leaks to the next user of a pooled connection.
    """
    bind = session.get_bind()
    if bind.dialect.name != "postgresql":
        return
    await session.execute(
        text(
            "SELECT set_config('hnsw.ef_search', :ef, true), "
            "set_config('hnsw.iterative_scan', 'relaxed_order', true)"
        ),
        {"ef": str(ef_search_for(top_k))},
    )


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
        await tune_index_scan(session, top_k)
        rows = (await session.execute(stmt)).all()
        found = [
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
        # An iterative scan returns batches, each in order but not the whole:
        # the fusion that follows ranks by position, so put them in order.
        found.sort(key=lambda c: c.score, reverse=True)
        return found

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
