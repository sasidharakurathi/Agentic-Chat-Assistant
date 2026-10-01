"""Hybrid retrieval: dense (pgvector cosine) + sparse (Postgres full-text),
fused with Reciprocal Rank Fusion, reranked, thresholded. Exactly the five
steps in ``docs/IMPLEMENTATION_PLAN.md`` §5.2 (query expansion, step 1
there, is explicitly optional/off-by-default in the plan and not built here
— ``RagRetrieval.max_queries`` is unused for now, a single-query v1).

This is the first thing that actually *reads* the knowledge base 2.1-2.5
built — nothing before this task could answer a question against it.
"""

from __future__ import annotations

import time
import uuid
from collections import OrderedDict
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.rag import Chunk, DataSource, Document
from app.observability.metrics import QUERY_CACHE, RETRIEVAL_DURATION
from app.rag.embedders import Embedder, get_embedder
from app.rag.rerankers import get_reranker
from app.rag.vectorstore import PgVectorStore, ScoredChunk
from app.schemas.assistant_config import RagRetrieval


@dataclass
class RetrievedChunk:
    chunk_id: uuid.UUID
    document_id: uuid.UUID
    data_source_id: uuid.UUID | None
    title: str
    uri: str | None
    content: str
    score: float
    page: int | None = None
    breadcrumb: list[str] = field(default_factory=list)
    # Everything below exists for citations (2.9): `source_type` decides which
    # kind of deep link is even possible (a file can be opened at a page, a
    # url has an original address, pasted text has neither), and the char
    # range is what "char range for text" in the plan's 5.3 actually means.
    source_type: str | None = None
    page_end: int | None = None
    char_start: int | None = None
    char_end: int | None = None
    ordinal: int | None = None


def rrf_fuse(
    dense: list[ScoredChunk], sparse: list[ScoredChunk], *, rrf_k: int
) -> list[ScoredChunk]:
    """Reciprocal Rank Fusion: score(d) = sum(1 / (rrf_k + rank_i(d))) over
    every ranked list a chunk appears in (1-indexed rank within that list).
    A chunk found by both dense and sparse search outscores one found by
    only one — that agreement is the whole point of hybrid search. Pure
    function, no DB — the fast unit-testable half of this module (see
    docs/IMPLEMENTATION_PLAN.md §11.1: "Unit: ... RRF fusion ...")."""
    scores: dict[uuid.UUID, float] = {}
    by_id: dict[uuid.UUID, ScoredChunk] = {}
    for ranked_list in (dense, sparse):
        for rank, chunk in enumerate(ranked_list, start=1):
            scores[chunk.id] = scores.get(chunk.id, 0.0) + 1.0 / (rrf_k + rank)
            by_id.setdefault(chunk.id, chunk)
    ordered_ids = sorted(scores, key=lambda cid: scores[cid], reverse=True)
    return [by_id[cid] for cid in ordered_ids]


async def _attach_source_metadata(
    session: AsyncSession, hits: list[tuple[ScoredChunk, float]]
) -> list[RetrievedChunk]:
    if not hits:
        return []
    chunk_ids = [c.id for c, _ in hits]
    rows = (
        await session.execute(
            select(Chunk.id, Document, DataSource)
            .join(Document, Document.id == Chunk.document_id)
            .join(DataSource, DataSource.id == Document.data_source_id)
            .where(Chunk.id.in_(chunk_ids))
        )
    ).all()
    meta = {chunk_id: (document, source) for chunk_id, document, source in rows}

    results = []
    for scored, score in hits:
        document, source = meta.get(scored.id, (None, None))
        title = (document.title if document else "") or (source.name if source else "")
        md = scored.metadata or {}
        results.append(
            RetrievedChunk(
                chunk_id=scored.id,
                document_id=scored.document_id,
                data_source_id=source.id if source else None,
                title=title,
                uri=source.uri if source else None,
                content=scored.content,
                score=score,
                page=md.get("page"),
                breadcrumb=md.get("breadcrumb", []),
                # `source.type` is a StrEnum; str() it here so nothing above
                # this layer has to import the model's enum to read it.
                source_type=str(source.type.value) if source else None,
                page_end=md.get("page_end"),
                # Chunks indexed before 2.9 have no offsets in their metadata
                # — these stay None and the UI simply shows no char range,
                # rather than inventing one. A reindex backfills them.
                char_start=md.get("start"),
                char_end=md.get("end"),
                ordinal=scored.ordinal,
            )
        )
    return results


async def retrieve(
    session: AsyncSession,
    *,
    assistant_id: uuid.UUID,
    query: str,
    config: RagRetrieval,
    source_ids: list[uuid.UUID] | None = None,
) -> list[RetrievedChunk]:
    started = time.perf_counter()
    try:
        return await _retrieve(session, assistant_id, query, config, source_ids)
    finally:
        # Timed whether it found anything or failed: a slow failure is still
        # time the turn spent here.
        RETRIEVAL_DURATION.observe(time.perf_counter() - started)


#: (embedder, query) -> (when it was stored, the vector). Oldest first.
_query_cache: OrderedDict[tuple[str, str], tuple[float, list[float]]] = OrderedDict()


async def query_embedding(embedder: Embedder, query: str) -> list[float]:
    """The query's vector, from memory when this process embedded the same
    text recently (task 6.5).

    Embedding the question is the one network call (or, offline, the one
    model run) in a search, and people ask the same things: a suggested
    question, a retry, the agent searching twice in a turn. A hit is free,
    so it records no usage.

    Keyed by the embedder as well as the text: a vector from one model
    means nothing to another. Not keyed by assistant or org: an embedding
    is a function of the text alone, and holds nothing of anyone's data.
    Per process, bounded, and short-lived.
    """
    size = settings.rag_query_cache_size
    if size <= 0:
        return await embedder.embed_query(query)
    key = (embedder.name, " ".join(query.split()))
    now = time.monotonic()
    hit = _query_cache.get(key)
    if hit is not None and now - hit[0] < settings.rag_query_cache_ttl_s:
        _query_cache.move_to_end(key)
        QUERY_CACHE.inc("hit")
        return hit[1]
    QUERY_CACHE.inc("miss")
    vector = await embedder.embed_query(query)
    _query_cache[key] = (now, vector)
    _query_cache.move_to_end(key)
    while len(_query_cache) > size:
        _query_cache.popitem(last=False)
    return vector


def clear_query_cache() -> None:
    _query_cache.clear()


async def _retrieve(
    session: AsyncSession,
    assistant_id: uuid.UUID,
    query: str,
    config: RagRetrieval,
    source_ids: list[uuid.UUID] | None,
) -> list[RetrievedChunk]:
    store = PgVectorStore()
    query_vector = await query_embedding(get_embedder(), query)

    dense = await store.query(
        session,
        assistant_id=assistant_id,
        embedding=query_vector,
        top_k=config.top_k_dense,
        source_ids=source_ids,
    )
    sparse: list[ScoredChunk] = []
    if config.hybrid:
        sparse = await store.query_sparse(
            session,
            assistant_id=assistant_id,
            query_text=query,
            top_k=config.top_k_sparse,
            source_ids=source_ids,
        )

    candidates = rrf_fuse(dense, sparse, rrf_k=config.rrf_k)
    if not candidates:
        return []

    reranker = get_reranker()
    ranked = await reranker.rerank(query, [c.content for c in candidates])

    kept = [
        (candidates[r.index], r.score)
        for r in ranked[: config.rerank_top_n]
        if r.score >= config.min_score
    ]
    return await _attach_source_metadata(session, kept)


__all__ = ["RetrievedChunk", "retrieve", "rrf_fuse"]
