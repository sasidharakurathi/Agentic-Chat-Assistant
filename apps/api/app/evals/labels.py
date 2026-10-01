"""Score retrieval for one labelled question (task 6.1).

Runs the real `retrieve()` for the question, with the assistant's own
retrieval settings, and compares what came back with the case's labels.

Labels name **sources** (by title or id) or **chunks** (by id). Sources are
what a builder can actually write down: they know "the refund policy
answers this", not which chunk ids the last reindex happened to produce.
With source labels the ranked list is the sources in the order their first
chunk appeared, so rank 1 means "the right document came first".
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.evals.metrics import score_case
from app.models.rag import DataSource
from app.rag.retrieve import RetrievedChunk, retrieve
from app.schemas.assistant_config import RagRetrieval
from app.schemas.evals import EvalLabels


async def resolve_sources(
    session: AsyncSession, assistant_id: uuid.UUID, wanted: list[str]
) -> tuple[set[str], list[str]]:
    """Source ids for the labels, and the labels that matched nothing (a
    renamed or deleted source would otherwise score 0 forever and look like
    a retrieval bug)."""
    rows = (
        await session.execute(
            select(DataSource.id, DataSource.name).where(DataSource.assistant_id == assistant_id)
        )
    ).all()
    by_key: dict[str, str] = {}
    for source_id, name in rows:
        by_key[str(source_id)] = str(source_id)
        by_key.setdefault(name.casefold(), str(source_id))
    found: set[str] = set()
    unknown: list[str] = []
    for label in wanted:
        hit = by_key.get(label) or by_key.get(label.casefold())
        if hit is None:
            unknown.append(label)
        else:
            found.add(hit)
    return found, unknown


def ranked_sources(hits: list[RetrievedChunk]) -> list[str]:
    """Each source once, where its best chunk ranked."""
    seen: dict[str, None] = {}
    for hit in hits:
        if hit.data_source_id is not None:
            seen.setdefault(str(hit.data_source_id), None)
    return list(seen)


async def score_retrieval(
    session: AsyncSession,
    *,
    assistant_id: uuid.UUID,
    question: str,
    labels: EvalLabels,
    config: RagRetrieval,
) -> dict[str, Any] | None:
    """The case's retrieval scores, or None when it has no labels."""
    if not labels.any:
        return None
    hits = await retrieve(session, assistant_id=assistant_id, query=question, config=config)
    unknown: list[str] = []
    if labels.relevant_chunk_ids:
        level, ranked = "chunk", [str(h.chunk_id) for h in hits]
        gold = set(labels.relevant_chunk_ids)
    else:
        level, ranked = "source", ranked_sources(hits)
        gold, unknown = await resolve_sources(session, assistant_id, labels.relevant_sources)
    k = config.rerank_top_n
    scored = score_case("", ranked, gold, k)
    return {
        "level": level,
        "k": k,
        "recall": round(scored.recall_at_k, 4),
        "reciprocal_rank": round(scored.reciprocal_rank, 4),
        "ndcg": round(scored.ndcg_at_k, 4),
        "hit": scored.hit,
        "retrieved": scored.retrieved,
        "relevant": scored.relevant,
        "unknown_labels": unknown,
    }


__all__ = ["ranked_sources", "resolve_sources", "score_retrieval"]
