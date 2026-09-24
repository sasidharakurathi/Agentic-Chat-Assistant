"""Voyage reranker: mocked HTTP, no network or API key needed. The local
cross-encoder reranker needs its model download — see
test_rag_local_models.py (``-m integration``)."""

from __future__ import annotations

import json
from collections.abc import Callable

import httpx
import pytest
from app.rag.rerankers.voyage import VoyageReranker


def _patch_httpx_client(
    monkeypatch: pytest.MonkeyPatch, handler: Callable[[httpx.Request], httpx.Response]
) -> None:
    real_client = httpx.AsyncClient

    def factory(*args: object, **kwargs: object) -> httpx.AsyncClient:
        kwargs["transport"] = httpx.MockTransport(handler)  # type: ignore[assignment]
        return real_client(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(httpx, "AsyncClient", factory)


async def test_rerank_sends_query_and_candidates_and_parses_scores(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        captured["auth"] = request.headers["authorization"]
        return httpx.Response(
            200,
            json={
                "data": [
                    {"index": 1, "relevance_score": 0.9},
                    {"index": 0, "relevance_score": 0.4},
                ]
            },
        )

    _patch_httpx_client(monkeypatch, handler)
    monkeypatch.setattr("app.rag.rerankers.voyage.settings.voyage_api_key", "test-key")

    reranker = VoyageReranker()
    results = await reranker.rerank("refund policy", ["irrelevant text", "you get a refund"])

    assert [r.index for r in results] == [1, 0]
    assert [r.score for r in results] == [0.9, 0.4]
    body = captured["body"]
    assert isinstance(body, dict)
    assert body["query"] == "refund policy"
    assert body["documents"] == ["irrelevant text", "you get a refund"]
    assert body["model"] == "rerank-2.5"
    assert captured["auth"] == "Bearer test-key"
