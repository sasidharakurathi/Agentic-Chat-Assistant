"""Run a labelled suite through the real `retrieve()` and score it.

This is the piece that turns "retrieval seems to work" into a number. It
calls the actual pipeline — real embedder, real hybrid search, real reranker
— so whatever it reports is what the assistant would really have found.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping

from sqlalchemy.ext.asyncio import AsyncSession

from app.evals.metrics import RetrievalScores, aggregate, score_case
from app.evals.suite import EvalSuite
from app.rag.retrieve import retrieve
from app.schemas.assistant_config import RagRetrieval


async def run_retrieval_suite(
    session: AsyncSession,
    *,
    assistant_id: uuid.UUID,
    suite: EvalSuite,
    key_to_chunk_ids: Mapping[str, set[uuid.UUID]],
    config: RagRetrieval | None = None,
    k: int | None = None,
) -> RetrievalScores:
    """Score every case in `suite`.

    ``key_to_chunk_ids`` is the bridge the suite format needs: labels name
    author-written corpus keys, but metrics compare chunk ids, and those only
    exist once the corpus has been ingested. A corpus passage can chunk into
    more than one row, so a key maps to a *set* — hitting any of them counts.

    `k` defaults to the retrieval config's `rerank_top_n`, i.e. "the results
    the agent would actually have been shown", which is the number that
    matters. Passing it explicitly lets you ask recall@1 of the same run.
    """
    cfg = config or RagRetrieval()
    cutoff = k or cfg.rerank_top_n

    scores = []
    for case in suite.cases:
        hits = await retrieve(session, assistant_id=assistant_id, query=case.input, config=cfg)
        retrieved = [str(h.chunk_id) for h in hits]
        relevant = {str(cid) for key in case.relevant for cid in key_to_chunk_ids.get(key, set())}
        scores.append(score_case(case.id, retrieved, relevant, cutoff))
    return aggregate(scores, cutoff)


def format_report(scores: RetrievalScores, suite_name: str = "") -> str:
    """A short human-readable summary — what you actually want printed when
    comparing two retrieval configurations."""
    header = f"{suite_name or 'suite'}: {scores.n} cases @k={scores.k}"
    body = (
        f"  recall@{scores.k}  {scores.recall_at_k:.3f}\n"
        f"  MRR          {scores.mrr:.3f}\n"
        f"  nDCG@{scores.k}    {scores.ndcg_at_k:.3f}"
    )
    misses = scores.misses
    tail = f"\n  missed: {', '.join(misses)}" if misses else ""
    return f"{header}\n{body}{tail}"


__all__ = ["format_report", "run_retrieval_suite"]
