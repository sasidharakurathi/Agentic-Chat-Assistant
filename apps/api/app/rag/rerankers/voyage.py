"""Real Voyage AI reranking over their REST API — same rationale as
``embedders/voyage.py``: plain ``httpx``, no SDK dependency.
"""

from __future__ import annotations

import httpx

from app.config import settings
from app.rag.rerankers.base import RankedResult
from app.rag.usage import estimate_tokens, record

_API_URL = "https://api.voyageai.com/v1/rerank"


class VoyageReranker:
    name = "voyage-rerank-2.5"

    def __init__(self, model: str = "rerank-2.5") -> None:
        self.model = model

    async def rerank(self, query: str, candidates: list[str]) -> list[RankedResult]:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                _API_URL,
                headers={"Authorization": f"Bearer {settings.voyage_api_key}"},
                json={"query": query, "documents": candidates, "model": self.model},
            )
            resp.raise_for_status()
            body = resp.json()
        tokens = (body.get("usage") or {}).get("total_tokens") or estimate_tokens(
            [query, *candidates]
        )
        record("rerank", self.model, int(tokens))
        # Voyage already returns results sorted by relevance_score desc.
        return [
            RankedResult(index=item["index"], score=item["relevance_score"])
            for item in body["data"]
        ]


__all__ = ["VoyageReranker"]
