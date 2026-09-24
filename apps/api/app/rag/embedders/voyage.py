"""Real Voyage AI embeddings over their REST API — plain ``httpx``, no
``voyageai`` SDK dependency; the API is simple enough that a dedicated
client isn't worth the extra dependency.

Requests are batched to Voyage's per-request limits (1,000 texts and 120K
tokens for voyage-3-large) and retried on rate limits. Everything used to go
in one request, so a source past ~120K tokens (a 150-page PDF) was refused
outright and could never be indexed.
"""

from __future__ import annotations

from collections.abc import Iterator

import httpx

from app.config import settings
from app.rag import EMBEDDING_DIM
from app.rag.http_retry import post_with_retry
from app.rag.usage import estimate_tokens, record

_API_URL = "https://api.voyageai.com/v1/embeddings"

#: Well inside Voyage's 1,000 texts / 120K tokens per request. The token
#: count here is an estimate (chars / 4) and Voyage's tokenizer can count
#: more for code or non-English text, hence the margin.
MAX_BATCH_TEXTS = 128
MAX_BATCH_TOKENS = 60_000


def batches(
    texts: list[str],
    *,
    max_texts: int = MAX_BATCH_TEXTS,
    max_tokens: int = MAX_BATCH_TOKENS,
) -> Iterator[list[str]]:
    """Consecutive runs of `texts` within both limits, in order. A single
    text over the token limit still goes alone (Voyage truncates it)."""
    batch: list[str] = []
    tokens = 0
    for text in texts:
        cost = estimate_tokens([text])
        if batch and (len(batch) >= max_texts or tokens + cost > max_tokens):
            yield batch
            batch, tokens = [], 0
        batch.append(text)
        tokens += cost
    if batch:
        yield batch


class VoyageEmbedder:
    name = "voyage-3-large"
    dim = EMBEDDING_DIM

    def __init__(self, model: str = "voyage-3-large") -> None:
        self.model = model

    async def _embed(self, texts: list[str], input_type: str) -> list[list[float]]:
        vectors: list[list[float]] = []
        async with httpx.AsyncClient(timeout=60.0) as client:
            for batch in batches(texts):
                resp = await post_with_retry(
                    client,
                    _API_URL,
                    headers={"Authorization": f"Bearer {settings.voyage_api_key}"},
                    json={"input": batch, "model": self.model, "input_type": input_type},
                )
                resp.raise_for_status()
                body = resp.json()
                tokens = (body.get("usage") or {}).get("total_tokens") or estimate_tokens(batch)
                record("embedding", self.model, int(tokens))
                vectors.extend(item["embedding"] for item in body["data"])
        return vectors

    async def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return await self._embed(texts, "document")

    async def embed_query(self, text: str) -> list[float]:
        results = await self._embed([text], "query")
        return results[0]


__all__ = ["MAX_BATCH_TEXTS", "MAX_BATCH_TOKENS", "VoyageEmbedder", "batches"]
