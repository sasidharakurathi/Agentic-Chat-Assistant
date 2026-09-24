"""Free, fully offline reranking via ``BAAI/bge-reranker-v2-m3`` (a
sentence-transformers ``CrossEncoder``) — the local counterpart to
``rerankers/voyage.py``, used under the same ``RAG_OFFLINE=1`` condition as
``embedders/local_bge.py``.
"""

from __future__ import annotations

import asyncio
from functools import lru_cache
from typing import TYPE_CHECKING

from app.rag.rerankers.base import RankedResult
from app.rag.usage import estimate_tokens, record

if TYPE_CHECKING:
    from sentence_transformers import CrossEncoder

_MODEL_NAME = "BAAI/bge-reranker-v2-m3"


@lru_cache
def _get_model() -> CrossEncoder:
    from sentence_transformers import CrossEncoder

    return CrossEncoder(_MODEL_NAME)


def _score(query: str, candidates: list[str]) -> list[RankedResult]:
    pairs = [(query, c) for c in candidates]
    scores = _get_model().predict(pairs)
    ranked = sorted(enumerate(scores), key=lambda pair: pair[1], reverse=True)
    return [RankedResult(index=i, score=float(s)) for i, s in ranked]


class LocalCrossEncoderReranker:
    name = "local-bge-reranker-v2-m3"

    async def rerank(self, query: str, candidates: list[str]) -> list[RankedResult]:
        record("rerank", self.name, estimate_tokens([query, *candidates]))
        return await asyncio.to_thread(_score, query, candidates)


__all__ = ["LocalCrossEncoderReranker"]
