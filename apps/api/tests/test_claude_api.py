"""Direct Claude API calls outside agent turns (`agent/claude_api.py`).

One-shot completions (summaries, titles) on the official SDK, over a mock
transport (`tests/claude_stub.py`): the requests, typed errors and retries
are the SDK's own, and nothing reaches the network.
"""

from __future__ import annotations

import anthropic
import pytest
from app.agent import claude_api
from app.agent import history as history_mod
from app.agent import titles as titles_mod
from app.agent.claude_api import MAX_RETRIES, Refused, complete
from app.agent.history import HistoryMessage
from app.agent.models import SUMMARY_MODEL, TITLE_MODEL
from app.config import settings
from tests import claude_stub


def test_the_client_uses_the_platform_key_and_bounded_retries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    key = "sk-ant-api03-" + "K" * 40
    monkeypatch.setattr(settings, "anthropic_api_key", key)
    api = claude_api._new_client(15.0)
    assert (api.api_key, api.max_retries, api.timeout) == (key, MAX_RETRIES, 15.0)


async def test_complete_sends_one_prompt_and_prices_the_answer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stub = claude_stub.install(
        monkeypatch,
        lambda r: claude_stub.message("  The answer.  ", tokens_in=2_000, tokens_out=100),
    )
    done = await complete("Question?", model="claude-haiku-4-5", max_tokens=64, timeout_s=9.0)
    (request,) = stub.requests
    assert request.url.path == "/v1/messages"
    assert request.headers["x-api-key"] == "sk-ant-stub"
    sent = claude_stub.body(request)
    assert (sent["model"], sent["max_tokens"]) == ("claude-haiku-4-5", 64)
    assert sent["messages"] == [{"role": "user", "content": "Question?"}]
    assert stub.timeouts == [9.0]
    assert (done.text, done.model, done.tokens_in, done.tokens_out) == (
        "The answer.",
        "claude-haiku-4-5",
        2_000,
        100,
    )
    # haiku is (1.00, 5.00) per Mtok
    assert done.cost_usd == pytest.approx(2_000 / 1e6 + 100 * 5 / 1e6)


async def test_a_refusal_raises_rather_than_passing_for_an_answer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    claude_stub.install(
        monkeypatch, lambda r: claude_stub.message("I can't.", stop_reason="refusal")
    )
    with pytest.raises(Refused):
        await complete("x", model="claude-haiku-4-5", max_tokens=10)


async def test_overloads_are_retried_by_the_sdk_then_raised_typed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stub = claude_stub.install(monkeypatch, lambda r: claude_stub.error(529, "overloaded_error"))
    with pytest.raises(anthropic.OverloadedError):
        await complete("x", model="claude-haiku-4-5", max_tokens=10)
    assert len(stub.requests) == 1 + MAX_RETRIES


async def test_a_rejected_key_is_not_retried(monkeypatch: pytest.MonkeyPatch) -> None:
    stub = claude_stub.install(
        monkeypatch, lambda r: claude_stub.error(401, "authentication_error")
    )
    with pytest.raises(anthropic.AuthenticationError):
        await complete("x", model="claude-haiku-4-5", max_tokens=10)
    assert len(stub.requests) == 1 and stub.slept == []


# ── the paid summarizer and titler ───────────────────────────


async def test_the_summarizer_folds_messages_into_the_previous_summary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stub = claude_stub.install(
        monkeypatch, lambda r: claude_stub.message("- wants a refund", tokens_out=12)
    )
    summary = await history_mod.HaikuSummarizer().summarize(
        "- ordered a lamp",
        [HistoryMessage("user", "It arrived broken."), HistoryMessage("assistant", "Sorry!")],
    )
    sent = claude_stub.body(stub.requests[0])
    assert sent["model"] == SUMMARY_MODEL
    prompt = claude_stub.prompt(stub.requests[0])
    assert "<summary>\n- ordered a lamp\n</summary>" in prompt
    assert '<message role="user">\nIt arrived broken.\n</message>' in prompt
    assert (summary.text, summary.model, summary.tokens_out) == (
        "- wants a refund",
        SUMMARY_MODEL,
        12,
    )
    assert summary.cost_usd > 0


async def test_the_titler_names_the_first_message(monkeypatch: pytest.MonkeyPatch) -> None:
    stub = claude_stub.install(monkeypatch, lambda r: claude_stub.message('"Broken lamp refund."'))
    title = await titles_mod.HaikuTitler().title("My lamp arrived broken, can I get a refund?")
    sent = claude_stub.body(stub.requests[0])
    assert (sent["model"], sent["max_tokens"]) == (TITLE_MODEL, 30)
    assert "<message>\nMy lamp arrived broken" in claude_stub.prompt(stub.requests[0])
    assert stub.timeouts == [15.0]
    assert title.text == "Broken lamp refund" and title.model == TITLE_MODEL
