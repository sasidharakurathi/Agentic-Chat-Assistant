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
import threading
from functools import lru_cache
from typing import TYPE_CHECKING

from app.rag import EMBEDDING_DIM
from app.rag.usage import estimate_tokens, record

if TYPE_CHECKING:
    from sentence_transformers import SentenceTransformer

_MODEL_NAME = "BAAI/bge-m3"

#: Texts per forward pass. sentence-transformers defaults to 32; with long
#: chunks (text without spaces tokenizes to ~2x the estimate) a batch of 32
#: held gigabytes of activations. Smaller batches cost little speed on a CPU.
BATCH_SIZE = 8

#: One encode at a time per process. PyTorch already spreads a single batch
#: across every core, so two ingestion jobs encoding at once (arq runs jobs
#: concurrently) got no faster, only twice the memory: a worker indexing two
#: large files climbed past 15 GB. Queries wait behind at most one batch.
_ENCODE_LOCK = threading.Lock()


@lru_cache
def _get_model() -> SentenceTransformer:
    from sentence_transformers import SentenceTransformer

    return SentenceTransformer(_MODEL_NAME)


def _encode(texts: list[str]) -> list[list[float]]:
    model = _get_model()
    with _ENCODE_LOCK:
        vectors = model.encode(texts, batch_size=BATCH_SIZE, normalize_embeddings=True)
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
