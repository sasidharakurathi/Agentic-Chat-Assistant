"""A word-overlap stand-in for the embedder, vector store and reranker, so
retrieval runs on SQLite with no model download (task 6.2).

Only these three ends are replaced. `retrieve()` itself is the real one:
it still asks for dense and sparse candidates, fuses them, reranks, applies
`min_score` and `rerank_top_n`, and loads each hit's document and source.

It is deliberately crude (hashed bags of stemmed words), and it is not a
measure of retrieval quality: that is `test_evals_retrieval.py`, on the
real models. It is only good enough that the right passage wins for the
fixture suite's questions, so the gate can tell when the plumbing around
retrieval breaks.
"""

from __future__ import annotations

import math
import re
import uuid
import zlib
from typing import Any

import pytest
from app.models.rag import Chunk
from app.rag.rerankers.base import RankedResult
from app.rag.vectorstore.base import ScoredChunk
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

DIM = 512
_STOP = frozenset(
    [
        "a",
        "an",
        "and",
        "are",
        "as",
        "at",
        "be",
        "by",
        "can",
        "do",
        "does",
        "for",
        "from",
        "how",
        "i",
        "in",
        "is",
        "it",
        "its",
        "my",
        "of",
        "on",
        "or",
        "the",
        "to",
        "what",
        "when",
        "which",
        "with",
        "you",
        "your",
    ]
)


def stems(text: str) -> list[str]:
    """Lowercased words, minus the common ones, cut to six letters: "refunds"
    and "refund", "expires" and "expire" meet."""
    return [w[:6] for w in re.findall(r"[a-z0-9]+", text.lower()) if w not in _STOP]


def embed(text: str) -> list[float]:
    vector = [0.0] * DIM
    for stem in stems(text):
        vector[zlib.crc32(stem.encode()) % DIM] += 1.0
    return vector


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    norm = math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b))
    return dot / norm if norm else 0.0


class LexicalEmbedder:
    name = "lexical-stub"

    async def embed_query(self, text: str) -> list[float]:
        return embed(text)

    async def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [embed(t) for t in texts]


class LexicalStore:
    """Reads the assistant's chunks from whatever database the session is
    on, and ranks them in Python."""

    async def _chunks(
        self, session: AsyncSession, assistant_id: uuid.UUID, source_ids: list[uuid.UUID] | None
    ) -> list[Chunk]:
        rows = (
            await session.scalars(select(Chunk).where(Chunk.assistant_id == assistant_id))
        ).all()
        assert source_ids is None, "the fixture suite doesn't narrow by source"
        return list(rows)

    @staticmethod
    def _scored(chunk: Chunk, score: float) -> ScoredChunk:
        return ScoredChunk(
            id=chunk.id,
            document_id=chunk.document_id,
            content=chunk.content,
            score=score,
            metadata=dict(chunk.chunk_metadata or {}),
            ordinal=chunk.ordinal,
        )

    async def query(
        self,
        session: AsyncSession,
        *,
        assistant_id: uuid.UUID,
        embedding: list[float],
        top_k: int,
        source_ids: list[uuid.UUID] | None = None,
    ) -> list[ScoredChunk]:
        chunks = await self._chunks(session, assistant_id, source_ids)
        ranked = sorted(
            ((c, _cosine(embedding, embed(c.content))) for c in chunks),
            key=lambda pair: pair[1],
            reverse=True,
        )
        return [self._scored(c, s) for c, s in ranked[:top_k]]

    async def query_sparse(
        self,
        session: AsyncSession,
        *,
        assistant_id: uuid.UUID,
        query_text: str,
        top_k: int,
        source_ids: list[uuid.UUID] | None = None,
    ) -> list[ScoredChunk]:
        wanted = set(stems(query_text))
        chunks = await self._chunks(session, assistant_id, source_ids)
        hits = [(c, len(wanted & set(stems(c.content)))) for c in chunks]
        ranked = sorted((h for h in hits if h[1] > 0), key=lambda pair: pair[1], reverse=True)
        return [self._scored(c, float(n)) for c, n in ranked[:top_k]]


class LexicalReranker:
    name = "lexical-stub"

    async def rerank(self, query: str, candidates: list[str]) -> list[RankedResult]:
        wanted = set(stems(query))
        scored = [
            RankedResult(index=i, score=len(wanted & set(stems(text))) / max(1, len(wanted)))
            for i, text in enumerate(candidates)
        ]
        return sorted(scored, key=lambda r: r.score, reverse=True)


def install(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Point `retrieve()` at the stand-ins. Returns them, so a test can
    break one on purpose."""
    parts: dict[str, Any] = {
        "store": LexicalStore(),
        "embedder": LexicalEmbedder(),
        "reranker": LexicalReranker(),
    }
    monkeypatch.setattr("app.rag.retrieve.PgVectorStore", lambda: parts["store"])
    monkeypatch.setattr("app.rag.retrieve.get_embedder", lambda: parts["embedder"])
    monkeypatch.setattr("app.rag.retrieve.get_reranker", lambda: parts["reranker"])
    return parts


__all__ = ["LexicalEmbedder", "LexicalReranker", "LexicalStore", "embed", "install", "stems"]
