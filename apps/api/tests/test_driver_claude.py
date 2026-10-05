"""`ClaudeSDKDriver`'s message mapping (F-2).

The real driver had no tests at all: its mapping lived inside a live-client
loop that needs the CLI. `_events_for` is that mapping pulled out as a pure
function, so it can be exercised with the SDK's own message dataclasses.

The regression it pins: tool results arrive in a `UserMessage`, and the
driver only ever looked at `AssistantMessage`, so on the real driver no
`tool_result` event was emitted at all. `FakeDriver` builds its events by
hand, which is why nothing caught it. (Confirmed end-to-end against the
bundled CLI and a local fake Messages API: frames were `tool_call`, then
nothing.)
"""

from __future__ import annotations

import pytest
from app.agent.driver import _events_for, _PartialText, _UsageLedger
from app.agent.events import ThinkingEvent, TokenEvent, ToolCallEvent, ToolResultEvent, UsageEvent
from claude_agent_sdk import (
    AssistantMessage,
    ResultMessage,
    StreamEvent,
    TextBlock,
    ThinkingBlock,
    ToolResultBlock,
    ToolUseBlock,
    UserMessage,
)


def _result(tool_use_id: str, text: str, *, is_error: bool = False) -> UserMessage:
    return UserMessage(
        content=[
            ToolResultBlock(
                tool_use_id=tool_use_id,
                content=[{"type": "text", "text": text}],
                is_error=is_error,
            )
        ]
    )


def test_a_tool_result_in_a_user_message_becomes_a_tool_result_event() -> None:
    (event,) = _events_for(_result("t1", "1 row(s) affected"), denied=set())
    assert isinstance(event, ToolResultEvent)
    assert (event.id, event.status, event.output) == ("t1", "success", "1 row(s) affected")


def test_an_error_result_is_labelled_error() -> None:
    (event,) = _events_for(_result("t1", "blocked: read-only", is_error=True), denied=set())
    assert event.status == "error"


def test_a_call_the_router_refused_is_labelled_denied_not_error() -> None:
    """A reviewer's "no" and a failure are different things to show a user;
    the CLI reports both as is_error, so the driver has to remember which
    calls `can_use_tool` refused."""
    (event,) = _events_for(
        _result("t1", "A human reviewer declined this action.", is_error=True), denied={"t1"}
    )
    assert event.status == "denied"


def test_several_results_in_one_message_each_become_an_event() -> None:
    msg = UserMessage(
        content=[
            ToolResultBlock(tool_use_id="a", content="one", is_error=False),
            ToolResultBlock(tool_use_id="b", content="two", is_error=False),
        ]
    )
    assert [e.id for e in _events_for(msg, denied=set())] == ["a", "b"]


def test_a_plain_text_user_message_produces_nothing() -> None:
    assert _events_for(UserMessage(content="hello"), denied=set()) == []


def test_assistant_text_and_tool_calls_still_map() -> None:
    msg = AssistantMessage(
        content=[
            TextBlock(text="Let me check."),
            ToolUseBlock(id="t1", name="mcp__caps__sql_query", input={"sql": "SELECT 1"}),
        ],
        model="claude-sonnet-5",
    )
    token, call = _events_for(msg, denied=set())
    assert isinstance(token, TokenEvent) and token.text == "Let me check."
    assert isinstance(call, ToolCallEvent) and call.input == {"sql": "SELECT 1"}


def _result_msg(tokens_in: int, tokens_out: int, cost: float) -> ResultMessage:
    return ResultMessage(
        subtype="success",
        duration_ms=10,
        duration_api_ms=8,
        is_error=False,
        num_turns=2,
        session_id="sess-1",
        total_cost_usd=cost,
        usage={"input_tokens": tokens_in, "output_tokens": tokens_out},
    )


def _assistant(message_id: str, tokens_in: int, tokens_out: int) -> AssistantMessage:
    return AssistantMessage(
        content=[TextBlock(text="x")],
        model="claude-sonnet-5",
        message_id=message_id,
        usage={"input_tokens": tokens_in, "output_tokens": tokens_out},
    )


def _usage(events: list) -> list[UsageEvent]:
    return [e for e in events if isinstance(e, UsageEvent)]


def test_the_result_message_becomes_usage() -> None:
    (event,) = _events_for(_result_msg(100, 20, 0.0123), denied=set())
    assert isinstance(event, UsageEvent)
    assert (event.tokens_in, event.tokens_out, event.cost_usd) == (100, 20, 0.0123)
    assert event.sdk_session_id == "sess-1"


# ── spend is reported as it is incurred (F-1) ────────────────
#
# Cost used to arrive only in the terminal ResultMessage. A turn cut short by
# a disconnect never receives one, so it was recorded as free although every
# model call it made was billed.


def test_each_completed_model_call_reports_its_spend() -> None:
    ledger = _UsageLedger()
    (spend,) = _usage(_events_for(_assistant("m1", 1000, 200), set(), ledger))
    assert (spend.tokens_in, spend.tokens_out) == (1000, 200)
    assert spend.cost_usd > 0


def test_a_repeated_message_is_not_billed_twice() -> None:
    """The CLI can emit several AssistantMessages for one API response, each
    repeating that response's usage."""
    ledger = _UsageLedger()
    first = _usage(_events_for(_assistant("m1", 1000, 200), set(), ledger))
    again = _usage(_events_for(_assistant("m1", 1000, 200), set(), ledger))
    assert len(first) == 1
    assert again == []


def test_an_interrupted_turn_still_knows_what_it_spent() -> None:
    """No ResultMessage ever arrives — the ledger is all there is."""
    ledger = _UsageLedger()
    emitted = [
        *_usage(_events_for(_assistant("m1", 1000, 200), set(), ledger)),
        *_usage(_events_for(_assistant("m2", 1500, 300), set(), ledger)),
    ]
    assert sum(e.tokens_in for e in emitted) == 2500
    assert sum(e.tokens_out for e in emitted) == 500
    assert sum(e.cost_usd for e in emitted) == pytest.approx(ledger.cost_usd)
    assert ledger.cost_usd > 0


def _partial(event: dict, parent: str | None = None) -> StreamEvent:
    return StreamEvent(uuid="u", session_id="s", event=event, parent_tool_use_id=parent)


def _deltas(message_id: str, *texts: str, kind: str = "text") -> list[StreamEvent]:
    key = "text_delta" if kind == "text" else "thinking_delta"
    field = "text" if kind == "text" else "thinking"
    return [
        _partial({"type": "message_start", "message": {"id": message_id}}),
        *(
            _partial({"type": "content_block_delta", "index": 0, "delta": {"type": key, field: t}})
            for t in texts
        ),
    ]


def test_text_streams_token_by_token_from_partial_events() -> None:
    """Found live: on the real driver the whole answer arrived as ONE token
    event at the very end. `include_partial_messages` was on, but the
    driver ignored the StreamEvents that carry the deltas."""
    partial = _PartialText()
    events = [
        e
        for m in _deltas("m1", "Hel", "lo ", "there")
        for e in _events_for(m, set(), partial=partial)
    ]
    assert [e.text for e in events if isinstance(e, TokenEvent)] == ["Hel", "lo ", "there"]


def test_the_finished_message_does_not_repeat_what_was_streamed() -> None:
    partial = _PartialText()
    for m in _deltas("m1", "Hello"):
        _events_for(m, set(), partial=partial)
    final = AssistantMessage(content=[TextBlock(text="Hello")], model="m", message_id="m1")
    assert [
        e for e in _events_for(final, set(), partial=partial) if isinstance(e, TokenEvent)
    ] == []


def test_a_message_that_never_streamed_still_shows_its_text() -> None:
    """Parity with before: anything without partials (e.g. a subagent's
    message) is emitted from the finished block, exactly as it used to be."""
    partial = _PartialText()
    for m in _deltas("m1", "streamed"):
        _events_for(m, set(), partial=partial)
    other = AssistantMessage(content=[TextBlock(text="whole")], model="m", message_id="m2")
    tokens = [
        e.text for e in _events_for(other, set(), partial=partial) if isinstance(e, TokenEvent)
    ]
    assert tokens == ["whole"]


def test_thinking_streams_too_and_is_not_repeated() -> None:
    partial = _PartialText()
    events = [
        e
        for m in _deltas("m1", "hmm", kind="thinking")
        for e in _events_for(m, set(), partial=partial)
    ]
    assert [e.text for e in events if isinstance(e, ThinkingEvent)] == ["hmm"]
    final = AssistantMessage(
        content=[ThinkingBlock(thinking="hmm", signature="x")], model="m", message_id="m1"
    )
    assert [
        e for e in _events_for(final, set(), partial=partial) if isinstance(e, ThinkingEvent)
    ] == []


def test_interleaved_subagent_streams_are_tracked_separately() -> None:
    """A subagent's deltas carry its parent_tool_use_id; its message in
    flight must not be confused with the main agent's."""
    partial = _PartialText()
    _events_for(
        _partial({"type": "message_start", "message": {"id": "main"}}), set(), partial=partial
    )
    _events_for(
        _partial({"type": "message_start", "message": {"id": "sub"}}, parent="t1"),
        set(),
        partial=partial,
    )
    _events_for(
        _partial(
            {"type": "content_block_delta", "delta": {"type": "text_delta", "text": "x"}},
            parent="t1",
        ),
        set(),
        partial=partial,
    )
    main_final = AssistantMessage(
        content=[TextBlock(text="main text")], model="m", message_id="main"
    )
    tokens = [
        e.text for e in _events_for(main_final, set(), partial=partial) if isinstance(e, TokenEvent)
    ]
    assert tokens == ["main text"], "the main message never streamed, so its text must still appear"


def test_a_zero_cost_from_the_sdk_does_not_erase_real_spend() -> None:
    """Observed live: the CLI reports total_cost_usd = 0 for a model it cannot
    price, and settling to it recorded a 40-token turn as free."""
    ledger = _UsageLedger()
    emitted = [
        *_usage(_events_for(_assistant("m1", 1000, 200), set(), ledger)),
        *_usage(_events_for(_result_msg(1000, 200, 0.0), set(), ledger)),
    ]
    assert sum(e.cost_usd for e in emitted) == pytest.approx(ledger.cost_usd)
    assert ledger.cost_usd > 0


def test_the_result_settles_totals_to_the_sdks_own_figures() -> None:
    """Estimates in flight, authority at the end: whatever was emitted along
    the way, the sum of every usage event equals what the SDK reports."""
    ledger = _UsageLedger()
    emitted = [
        *_usage(_events_for(_assistant("m1", 1000, 200), set(), ledger)),
        *_usage(_events_for(_assistant("m2", 1500, 300), set(), ledger)),
        *_usage(_events_for(_result_msg(2500, 500, 0.0421), set(), ledger)),
    ]
    assert sum(e.tokens_in for e in emitted) == 2500
    assert sum(e.tokens_out for e in emitted) == 500
    assert sum(e.cost_usd for e in emitted) == pytest.approx(0.0421)


# ── cached input is input read ───────────────────────────────
#
# With prompt caching most of a turn's input is served from the cache, and
# the usage reports it apart from `input_tokens`. Counting `input_tokens`
# alone showed "2 in" for an answer that read thousands of tokens (found in
# the first test drive against the real model).


def _cached(message_id: str, tokens_in: int, cache_read: int, cache_write: int, out: int):
    return AssistantMessage(
        content=[TextBlock(text="x")],
        model="claude-sonnet-5",
        message_id=message_id,
        usage={
            "input_tokens": tokens_in,
            "cache_read_input_tokens": cache_read,
            "cache_creation_input_tokens": cache_write,
            "output_tokens": out,
        },
    )


def test_cached_input_counts_as_input_read_once() -> None:
    ledger = _UsageLedger()
    (spend,) = _usage(_events_for(_cached("m1", 2, 2400, 300, 369), set(), ledger))
    assert (spend.tokens_in, spend.tokens_out) == (2702, 369)
    # The CLI repeating the same response adds nothing.
    assert _usage(_events_for(_cached("m1", 2, 2400, 300, 369), set(), ledger)) == []
    assert ledger.tokens_in == 2702


def test_the_result_settles_input_including_the_cache() -> None:
    ledger = _UsageLedger()
    result = _result_msg(2, 369, 0.0151)
    result.usage.update({"cache_read_input_tokens": 2400, "cache_creation_input_tokens": 300})
    emitted = [
        *_usage(_events_for(_cached("m1", 2, 2400, 300, 369), set(), ledger)),
        *_usage(_events_for(result, set(), ledger)),
    ]
    assert sum(e.tokens_in for e in emitted) == 2702
    assert sum(e.tokens_out for e in emitted) == 369
    assert sum(e.cost_usd for e in emitted) == pytest.approx(0.0151)


def test_a_result_alone_counts_its_cached_input() -> None:
    """No per-call messages seen (the CLI sent only the result): the settle
    step is all there is, and it must count the cache too."""
    result = _result_msg(2, 369, 0.0151)
    result.usage.update({"cache_read_input_tokens": 2400, "cache_creation_input_tokens": 300})
    (event,) = _usage(_events_for(result, set(), _UsageLedger()))
    assert (event.tokens_in, event.tokens_out) == (2702, 369)
