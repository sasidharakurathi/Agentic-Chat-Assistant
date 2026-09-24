"""Voyage embedder: mocked HTTP, no network or API key needed. The local
bge-m3 embedder needs its ~2GB model — see test_rag_local_models.py
(``-m integration``)."""

from __future__ import annotations

import json
from collections.abc import Callable

import httpx
import pytest
from app.rag.embedders.voyage import VoyageEmbedder


def _patch_httpx_client(
    monkeypatch: pytest.MonkeyPatch, handler: Callable[[httpx.Request], httpx.Response]
) -> None:
    real_client = httpx.AsyncClient

    def factory(*args: object, **kwargs: object) -> httpx.AsyncClient:
        kwargs["transport"] = httpx.MockTransport(handler)  # type: ignore[assignment]
        return real_client(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(httpx, "AsyncClient", factory)


async def test_embed_documents_sends_document_input_type(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["body"] = json.loads(request.content)
        captured["auth"] = request.headers["authorization"]
        return httpx.Response(
            200, json={"data": [{"embedding": [0.1, 0.2]}, {"embedding": [0.3, 0.4]}]}
        )

    _patch_httpx_client(monkeypatch, handler)
    monkeypatch.setattr("app.rag.embedders.voyage.settings.voyage_api_key", "test-key")

    embedder = VoyageEmbedder()
    result = await embedder.embed_documents(["doc one", "doc two"])

    assert result == [[0.1, 0.2], [0.3, 0.4]]
    assert captured["url"] == "https://api.voyageai.com/v1/embeddings"
    assert captured["body"] == {
        "input": ["doc one", "doc two"],
        "model": "voyage-3-large",
        "input_type": "document",
    }
    assert captured["auth"] == "Bearer test-key"


async def test_embed_query_sends_query_input_type_and_unwraps_single_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json={"data": [{"embedding": [0.5, 0.6]}]})

    _patch_httpx_client(monkeypatch, handler)
    monkeypatch.setattr("app.rag.embedders.voyage.settings.voyage_api_key", "test-key")

    embedder = VoyageEmbedder()
    result = await embedder.embed_query("what is the refund policy?")

    assert result == [0.5, 0.6]
    body = captured["body"]
    assert isinstance(body, dict)
    assert body["input_type"] == "query"
    assert body["input"] == ["what is the refund policy?"]


async def test_raises_on_error_response(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": "invalid api key"})

    _patch_httpx_client(monkeypatch, handler)
    monkeypatch.setattr("app.rag.embedders.voyage.settings.voyage_api_key", "bad-key")

    embedder = VoyageEmbedder()
    with pytest.raises(httpx.HTTPStatusError):
        await embedder.embed_query("hello")


# ── batching and retry (task 2.6) ────────────────────────────


def test_batches_respect_both_the_count_and_the_token_limit() -> None:
    from app.rag.embedders.voyage import batches

    by_count = list(batches(["x"] * 300, max_texts=128, max_tokens=10_000_000))
    assert [len(b) for b in by_count] == [128, 128, 44]

    big = "y" * 4000  # ~1000 tokens each
    by_tokens = list(batches([big] * 10, max_texts=1000, max_tokens=3000))
    assert [len(b) for b in by_tokens] == [3, 3, 3, 1]

    huge = "z" * 400_000  # over the limit on its own: still sent, alone
    assert [len(b) for b in batches(["a", huge, "b"], max_tokens=1000)] == [1, 1, 1]


async def test_a_large_document_is_embedded_in_several_requests_in_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Everything used to go in one request, which Voyage refuses past 1,000
    texts or 120K tokens: a big PDF could never be indexed."""
    sizes: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        texts = json.loads(request.content)["input"]
        sizes.append(len(texts))
        return httpx.Response(
            200,
            json={
                "data": [{"embedding": [float(t)]} for t in texts],
                "usage": {"total_tokens": len(texts)},
            },
        )

    _patch_httpx_client(monkeypatch, handler)
    monkeypatch.setattr("app.rag.embedders.voyage.settings.voyage_api_key", "test-key")
    texts = [str(i) for i in range(300)]
    vectors = await VoyageEmbedder().embed_documents(texts)
    assert sizes == [128, 128, 44]
    assert vectors == [[float(i)] for i in range(300)], "order preserved across batches"


async def test_a_rate_limited_batch_is_retried(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = {"n": 0}
    slept: list[float] = []

    async def fake_sleep(delay: float) -> None:
        slept.append(delay)

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(429, headers={"retry-after": "2"}, json={"detail": "rate"})
        return httpx.Response(200, json={"data": [{"embedding": [1.0]}]})

    monkeypatch.setattr("app.rag.http_retry._sleep", fake_sleep)
    _patch_httpx_client(monkeypatch, handler)
    monkeypatch.setattr("app.rag.embedders.voyage.settings.voyage_api_key", "test-key")
    assert await VoyageEmbedder().embed_documents(["a"]) == [[1.0]]
    assert slept == [2.0]
