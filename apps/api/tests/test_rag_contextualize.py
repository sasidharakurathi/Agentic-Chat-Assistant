"""Contextual-retrieval prefixes (task 2.6).

The bulk of this file is about the *gate*, not the prompt. This is the first
thing in the project that spends money without a user pressing send — it
fires on a file upload, once per chunk — so "it is off unless three separate
things agree" is the property worth pinning hardest. The Haiku call itself is
exercised through `httpx.MockTransport`: no network, no key, no spend.
"""

from __future__ import annotations

import httpx
import pytest
from app.config import settings
from app.rag.contextualize import (
    ContextResult,
    HaikuContextualizer,
    NullContextualizer,
    apply_prefix,
    get_contextualizer,
)


def _mock_client(handler: object) -> object:
    """Patch httpx.AsyncClient so the contextualizer's own internally-built
    client is the mocked one (same trick test_rag_embedders.py uses)."""
    transport = httpx.MockTransport(handler)  # type: ignore[arg-type]

    class _Client(httpx.AsyncClient):
        def __init__(self, *a: object, **kw: object) -> None:
            kw.pop("transport", None)
            super().__init__(*a, transport=transport, **kw)  # type: ignore[arg-type]

    return _Client


def _reply(text: str, tokens_in: int = 500, tokens_out: int = 20) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "content": [{"type": "text", "text": text}],
            "usage": {"input_tokens": tokens_in, "output_tokens": tokens_out},
        },
    )


# ── the spend gate ───────────────────────────────────────────


def test_disabled_in_config_means_no_calls(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "rag_offline", False)
    monkeypatch.setattr(settings, "agent_driver", "claude")
    monkeypatch.setattr(settings, "app_env", "dev")
    monkeypatch.setattr(settings, "anthropic_api_key", "sk-real")
    assert isinstance(get_contextualizer(False), NullContextualizer)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("rag_offline", True),  # "no paid RAG calls"
        ("agent_driver", "fake"),  # "no real model calls"
        ("anthropic_api_key", ""),  # nothing to call with
        ("app_env", "test"),  # never from the suite
    ],
)
def test_any_single_veto_forces_the_free_path(
    monkeypatch: pytest.MonkeyPatch, field: str, value: object
) -> None:
    """Each of these independently means "don't spend" — none of them should
    need the others' help to stop a paid call."""
    monkeypatch.setattr(settings, "rag_offline", False)
    monkeypatch.setattr(settings, "agent_driver", "claude")
    monkeypatch.setattr(settings, "app_env", "dev")
    monkeypatch.setattr(settings, "anthropic_api_key", "sk-real")
    monkeypatch.setattr(settings, field, value)
    assert isinstance(get_contextualizer(True), NullContextualizer)


def test_all_three_agreeing_is_what_enables_it(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "rag_offline", False)
    monkeypatch.setattr(settings, "agent_driver", "claude")
    monkeypatch.setattr(settings, "app_env", "dev")
    monkeypatch.setattr(settings, "anthropic_api_key", "sk-real")
    assert isinstance(get_contextualizer(True), HaikuContextualizer)


def test_the_projects_pinned_env_keeps_it_off(monkeypatch: pytest.MonkeyPatch) -> None:
    """`.env` pins AGENT_DRIVER=fake and RAG_OFFLINE=1 for cost safety; with
    those set, even an assistant asking for contextualisation gets the free
    path."""
    monkeypatch.setattr(settings, "rag_offline", True)
    monkeypatch.setattr(settings, "agent_driver", "fake")
    monkeypatch.setattr(settings, "anthropic_api_key", "sk-real")
    assert get_contextualizer(True).enabled is False


# ── the free path ────────────────────────────────────────────


async def test_null_contextualizer_returns_one_none_per_chunk() -> None:
    result = await NullContextualizer().contextualize("doc", ["a", "b", "c"])
    assert result.prefixes == [None, None, None]
    assert result.cost_usd == 0.0
    assert result.written == 0


def test_apply_prefix_is_a_no_op_without_a_prefix() -> None:
    assert apply_prefix(None, "chunk text") == "chunk text"
    assert apply_prefix("", "chunk text") == "chunk text"
    assert apply_prefix("Context.", "chunk text") == "Context.\n\nchunk text"


# ── the paid path, mocked ────────────────────────────────────


def _tagged(*lines: str) -> str:
    return "\n".join(f'<context index="{i}">{line}</context>' for i, line in enumerate(lines, 1))


@pytest.fixture(autouse=True)
def _no_real_sleep(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    slept: list[float] = []

    async def fake_sleep(delay: float) -> None:
        slept.append(delay)

    monkeypatch.setattr("app.rag.http_retry._sleep", fake_sleep)
    return slept


async def test_chunks_are_batched_into_one_call_and_costs_are_summed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The document excerpt is most of every request; sending it once for a
    batch of chunks instead of once per chunk is what "batched" buys."""
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        import json as _json

        body = _json.loads(request.content)
        seen.append(body["messages"][0]["content"])
        return _reply(_tagged("About alpha.", "About beta."), tokens_in=1000, tokens_out=10)

    monkeypatch.setattr(httpx, "AsyncClient", _mock_client(handler))
    monkeypatch.setattr(settings, "anthropic_api_key", "sk-test")

    result = await HaikuContextualizer().contextualize("THE DOCUMENT", ["alpha", "beta"])

    assert result.prefixes == ["About alpha.", "About beta."]
    assert len(seen) == 1, "one request for both chunks"
    assert seen[0].count("THE DOCUMENT") == 1
    assert '<chunk index="1">\nalpha' in seen[0] and '<chunk index="2">\nbeta' in seen[0]
    # haiku is (1.00, 5.00) per Mtok
    assert (result.tokens_in, result.tokens_out) == (1000, 10)
    assert result.cost_usd == pytest.approx(1000 / 1e6 * 1.0 + 10 / 1e6 * 5.0)


async def test_batches_are_capped_at_the_batch_size(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return _reply(_tagged(*["c"] * 8))

    monkeypatch.setattr(httpx, "AsyncClient", _mock_client(handler))
    monkeypatch.setattr(settings, "anthropic_api_key", "sk-test")
    result = await HaikuContextualizer(batch_size=8).contextualize(
        "doc", [str(i) for i in range(20)]
    )
    assert calls["n"] == 3
    assert len(result.prefixes) == 20


async def test_a_line_the_model_left_out_is_no_prefix_not_a_shifted_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reply = '<context index="1">one</context>\n<context index="3">three</context>'
    monkeypatch.setattr(httpx, "AsyncClient", _mock_client(lambda r: _reply(reply)))
    monkeypatch.setattr(settings, "anthropic_api_key", "sk-test")
    result = await HaikuContextualizer().contextualize("doc", ["a", "b", "c"])
    assert result.prefixes == ["one", None, "three"]


async def test_a_rate_limit_is_retried_after_the_time_it_asks_for(
    monkeypatch: pytest.MonkeyPatch, _no_real_sleep: list[float]
) -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(429, headers={"retry-after": "7"}, json={"error": "rate"})
        return _reply(_tagged("ctx"))

    monkeypatch.setattr(httpx, "AsyncClient", _mock_client(handler))
    monkeypatch.setattr(settings, "anthropic_api_key", "sk-test")
    result = await HaikuContextualizer().contextualize("doc", ["a"])
    assert result.prefixes == ["ctx"]
    assert _no_real_sleep == [7.0]
    assert result.failed == 0


async def test_a_batch_that_keeps_failing_does_not_lose_the_others(
    monkeypatch: pytest.MonkeyPatch, _no_real_sleep: list[float]
) -> None:
    """A failed prefix costs a little retrieval quality; a raised exception
    would cost the user the entire ingestion."""

    def handler(request: httpx.Request) -> httpx.Response:
        import json as _json

        prompt = _json.loads(request.content)["messages"][0]["content"]
        if '<chunk index="1">\nb\n' in prompt:
            return httpx.Response(529, json={"error": "overloaded"})
        return _reply(_tagged("ok"))

    monkeypatch.setattr(httpx, "AsyncClient", _mock_client(handler))
    monkeypatch.setattr(settings, "anthropic_api_key", "sk-test")

    result = await HaikuContextualizer(concurrency=1, batch_size=1).contextualize(
        "doc", ["a", "b", "c"]
    )
    assert result.prefixes == ["ok", None, "ok"]
    assert result.failed == 1
    assert len(_no_real_sleep) == 4, "retried before giving up"


def test_the_estimate_scales_with_batches_not_chunks() -> None:
    ctx = HaikuContextualizer(batch_size=8)
    doc = "d" * 6000
    one_batch = ctx.estimate_cost(doc, ["c" * 400] * 8)
    per_chunk = HaikuContextualizer(batch_size=1).estimate_cost(doc, ["c" * 400] * 8)
    assert 0 < one_batch < per_chunk / 3


async def test_an_empty_response_becomes_no_prefix_not_an_empty_string(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(httpx, "AsyncClient", _mock_client(lambda r: _reply("   ")))
    monkeypatch.setattr(settings, "anthropic_api_key", "sk-test")
    result = await HaikuContextualizer().contextualize("doc", ["a"])
    assert result.prefixes == [None]


async def test_no_chunks_means_no_calls_at_all(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover
        raise AssertionError("should not have been called")

    monkeypatch.setattr(httpx, "AsyncClient", _mock_client(handler))
    assert await HaikuContextualizer().contextualize("doc", []) == ContextResult(prefixes=[])


async def test_the_document_shown_to_the_model_is_truncated(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A per-chunk call that ships the whole document would make ingesting a
    large PDF quadratic in cost."""
    from app.rag.contextualize import DOC_CONTEXT_CHARS

    sizes: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        import json as _json

        sizes.append(len(_json.loads(request.content)["messages"][0]["content"]))
        return _reply("ctx")

    monkeypatch.setattr(httpx, "AsyncClient", _mock_client(handler))
    monkeypatch.setattr(settings, "anthropic_api_key", "sk-test")
    await HaikuContextualizer().contextualize("x" * (DOC_CONTEXT_CHARS * 4), ["chunk"])
    assert sizes[0] < DOC_CONTEXT_CHARS * 2
