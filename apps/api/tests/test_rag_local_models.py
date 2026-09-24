"""The *real* local embedder/reranker — actually loads BAAI/bge-m3 and
BAAI/bge-reranker-v2-m3 (~2GB combined, downloaded once and cached by
sentence-transformers). Deselected by default for the same reason
test_rag_vectorstore.py is: real infra, not always available or fast.
Run with ``pytest -m integration``.
"""

from __future__ import annotations

import pytest
from app.rag import EMBEDDING_DIM
from app.rag.embedders.local_bge import LocalBgeEmbedder
from app.rag.rerankers.local import LocalCrossEncoderReranker

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def embedder() -> LocalBgeEmbedder:
    try:
        e = LocalBgeEmbedder()
        import asyncio

        asyncio.run(e.embed_query("warm up"))
        return e
    except Exception as exc:  # pragma: no cover - environment-dependent
        pytest.skip(f"bge-m3 unavailable (no network / no disk space?): {exc}")


async def test_embeddings_have_the_configured_dimension(embedder: LocalBgeEmbedder) -> None:
    vec = await embedder.embed_query("what is the refund policy?")
    assert len(vec) == EMBEDDING_DIM


async def test_similar_texts_score_higher_than_unrelated_ones(embedder: LocalBgeEmbedder) -> None:
    import math

    def cosine(a: list[float], b: list[float]) -> float:
        dot = sum(x * y for x, y in zip(a, b, strict=True))
        na = math.sqrt(sum(x * x for x in a))
        nb = math.sqrt(sum(y * y for y in b))
        return dot / (na * nb)

    query = await embedder.embed_query("How do I reset my password?")
    [related, unrelated] = await embedder.embed_documents(
        [
            "To reset your password, click 'forgot password' on the login page.",
            "Our office is located at 123 Main Street, open 9 to 5.",
        ]
    )
    assert cosine(query, related) > cosine(query, unrelated)


async def test_reranker_orders_the_relevant_candidate_first() -> None:
    try:
        reranker = LocalCrossEncoderReranker()
        results = await reranker.rerank(
            "How do I reset my password?",
            [
                "Our office is located at 123 Main Street, open 9 to 5.",
                "To reset your password, click 'forgot password' on the login page.",
            ],
        )
    except Exception as exc:  # pragma: no cover - environment-dependent
        pytest.skip(f"bge-reranker unavailable (no network / no disk space?): {exc}")

    assert results[0].index == 1
