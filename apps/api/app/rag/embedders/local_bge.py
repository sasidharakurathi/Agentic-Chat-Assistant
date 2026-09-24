"""Free, fully offline embeddings via ``BAAI/bge-m3`` (sentence-transformers).
Used when ``RAG_OFFLINE=1`` or no Voyage key is configured — this is what
lets RAG be developed and tested without spending any API credits.

``bge-m3``'s native output is 1024-d, which happens to match
``EMBEDDING_DIM`` (fixed for ``voyage-3-large``) exactly — no per-embedder
dimension handling needed anywhere else in the pipeline.

The model (~2GB) downloads on first use and is cached by
``sentence-transformers`` under the user's home directory; nothing here
triggers that download at import time, only on first ``embed_*`` call.
"""

from __future__ import annotations

import asyncio
from functools import lru_cache
from typing import TYPE_CHECKING

from app.rag import EMBEDDING_DIM
from app.rag.usage import estimate_tokens, record

if TYPE_CHECKING:
    from sentence_transformers import SentenceTransformer

_MODEL_NAME = "BAAI/bge-m3"


@lru_cache
def _get_model() -> SentenceTransformer:
    from sentence_transformers import SentenceTransformer

    return SentenceTransformer(_MODEL_NAME)


def _encode(texts: list[str]) -> list[list[float]]:
    vectors = _get_model().encode(texts, normalize_embeddings=True)
    return [v.tolist() for v in vectors]


class LocalBgeEmbedder:
    name = "local-bge-m3"
    dim = EMBEDDING_DIM

    async def embed_documents(self, texts: list[str]) -> list[list[float]]:
        record("embedding", self.name, estimate_tokens(texts))
        return await asyncio.to_thread(_encode, texts)

    async def embed_query(self, text: str) -> list[float]:
        record("embedding", self.name, estimate_tokens([text]))
        result = await asyncio.to_thread(_encode, [text])
        return result[0]


__all__ = ["LocalBgeEmbedder"]
