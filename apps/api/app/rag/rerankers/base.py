"""``Reranker``: re-scores a candidate set against the query (Voyage
``rerank-2.5`` in v1, a local cross-encoder for ``RAG_OFFLINE``). Concrete
implementations land in task 2.4; ``retrieve.py`` (2.7) is the only caller.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass
class RankedResult:
    index: int  # position in the candidates list passed to rerank()
    score: float


class Reranker(Protocol):
    name: str

    async def rerank(self, query: str, candidates: list[str]) -> list[RankedResult]:
        """Return candidates' new ranking, best first. Callers map ``index``
        back onto their own candidate objects — this stays text-in,
        score-out so it has no dependency on ``ScoredChunk`` or the DB."""
        ...


__all__ = ["RankedResult", "Reranker"]
