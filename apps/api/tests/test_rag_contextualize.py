"""Contextual-retrieval prefixes (task 2.6).

The bulk of this file is about the *gate*, not the prompt. This is the first
thing in the project that spends money without a user pressing send — it
fires on a file upload, once per chunk — so "it is off unless three separate
things agree" is the property worth pinning hardest. The Haiku call itself
runs on the official SDK over a mock transport (`tests/claude_stub.py`): no
network, no key, no spend.
"""

from __future__ import annotations

from typing import Any

import pytest
from app.agent.claude_api import MAX_RETRIES
from app.config import settings
from app.rag.contextualize import (
    ContextResult,
    HaikuContextualizer,
    NullContextualizer,
    apply_prefix,
    get_contextualizer,
)
from tests import claude_stub

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


# ── the paid path, on a mock transport ───────────────────────


def _tagged(*lines: str) -> str:
    return "\n".join(f'<context index="{i}">{line}</context>' for i, line in enumerate(lines, 1))


async def test_chunks_are_batched_into_one_call_and_costs_are_summed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The document excerpt is most of every request; sending it once for a
    batch of chunks instead of once per chunk is what "batched" buys."""
    stub = claude_stub.install(
        monkeypatch,
        lambda r: claude_stub.message(
            _tagged("About alpha.", "About beta."), tokens_in=1000, tokens_out=10
        ),
    )
    result = await HaikuContextualizer().contextualize("THE DOCUMENT", ["alpha", "beta"])

    assert result.prefixes == ["About alpha.", "About beta."]
    (request,) = stub.requests
    sent = claude_stub.body(request)
    assert (sent["model"], sent["max_tokens"]) == ("claude-haiku-4-5", 300)
    prompt = claude_stub.prompt(request)
    assert prompt.count("THE DOCUMENT") == 1
    assert '<chunk index="1">\nalpha' in prompt and '<chunk index="2">\nbeta' in prompt
    # haiku is (1.00, 5.00) per Mtok
    assert (result.tokens_in, result.tokens_out) == (1000, 10)
    assert result.cost_usd == pytest.approx(1000 / 1e6 * 1.0 + 10 / 1e6 * 5.0)
    assert stub.timeouts == [60.0], "one client for the whole document"


async def test_batches_are_capped_at_the_batch_size(monkeypatch: pytest.MonkeyPatch) -> None:
    stub = claude_stub.install(monkeypatch, lambda r: claude_stub.message(_tagged(*["c"] * 8)))
    result = await HaikuContextualizer(batch_size=8).contextualize(
        "doc", [str(i) for i in range(20)]
    )
    assert len(stub.requests) == 3
    assert len(result.prefixes) == 20


async def test_a_line_the_model_left_out_is_no_prefix_not_a_shifted_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reply = '<context index="1">one</context>\n<context index="3">three</context>'
    claude_stub.install(monkeypatch, lambda r: claude_stub.message(reply))
    result = await HaikuContextualizer().contextualize("doc", ["a", "b", "c"])
    assert result.prefixes == ["one", None, "three"]


async def test_a_rate_limit_is_retried_after_the_time_it_asks_for(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    answers = iter(
        [
            claude_stub.error(429, "rate_limit_error", {"retry-after": "7"}),
            claude_stub.message("ctx"),
        ]
    )
    stub = claude_stub.install(monkeypatch, lambda r: next(answers))
    result = await HaikuContextualizer().contextualize("doc", ["a"])
    assert result.prefixes == ["ctx"]
    assert stub.slept == [7.0]
    assert result.failed == 0


async def test_a_batch_that_keeps_failing_does_not_lose_the_others(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failed prefix costs a little retrieval quality; a raised exception
    would cost the user the entire ingestion."""

    def handler(request: Any) -> Any:
        if '<chunk index="1">\nb\n' in claude_stub.prompt(request):
            return claude_stub.error(529, "overloaded_error")
        return claude_stub.message(_tagged("ok"))

    stub = claude_stub.install(monkeypatch, handler)
    result = await HaikuContextualizer(concurrency=1, batch_size=1).contextualize(
        "doc", ["a", "b", "c"]
    )
    assert result.prefixes == ["ok", None, "ok"]
    assert result.failed == 1
    assert len(stub.slept) == MAX_RETRIES, "retried before giving up"
    assert len(stub.requests) == 3 + MAX_RETRIES


async def test_a_refusal_is_no_prefix_even_for_one_chunk(monkeypatch: pytest.MonkeyPatch) -> None:
    """A one-chunk answer without tags is taken as the line itself, so a
    refusal's text must not get that far."""
    claude_stub.install(
        monkeypatch,
        lambda r: claude_stub.message("I can't help with that.", stop_reason="refusal"),
    )
    result = await HaikuContextualizer().contextualize("doc", ["a"])
    assert result.prefixes == [None]
    assert result.failed == 1
    assert result.tokens_in == 500, "a refusal is still billed"


def test_the_estimate_scales_with_batches_not_chunks() -> None:
    ctx = HaikuContextualizer(batch_size=8)
    doc = "d" * 6000
    one_batch = ctx.estimate_cost(doc, ["c" * 400] * 8)
    per_chunk = HaikuContextualizer(batch_size=1).estimate_cost(doc, ["c" * 400] * 8)
    assert 0 < one_batch < per_chunk / 3


async def test_an_empty_response_becomes_no_prefix_not_an_empty_string(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    claude_stub.install(monkeypatch, lambda r: claude_stub.message("   "))
    result = await HaikuContextualizer().contextualize("doc", ["a"])
    assert result.prefixes == [None]


async def test_no_chunks_means_no_calls_at_all(monkeypatch: pytest.MonkeyPatch) -> None:
    stub = claude_stub.install(monkeypatch, lambda r: pytest.fail("should not have been called"))
    assert await HaikuContextualizer().contextualize("doc", []) == ContextResult(prefixes=[])
    assert stub.timeouts == [], "not even a client"


async def test_the_document_shown_to_the_model_is_truncated(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A per-chunk call that ships the whole document would make ingesting a
    large PDF quadratic in cost."""
    from app.rag.contextualize import DOC_CONTEXT_CHARS

    stub = claude_stub.install(monkeypatch, lambda r: claude_stub.message("ctx"))
    await HaikuContextualizer().contextualize("x" * (DOC_CONTEXT_CHARS * 4), ["chunk"])
    assert len(claude_stub.prompt(stub.requests[0])) < DOC_CONTEXT_CHARS * 2
