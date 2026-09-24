"""max_turns is enforced by the platform and surfaced to the client (task 1.8).

Before: the limit was only forwarded to the SDK, and when the SDK stopped on it
the turn ended silently, with an answer that simply stopped. Now each model call
is counted as it happens (from the per-call usage reports), a turn over the limit
is stopped by the runtime, and both paths send an `error` event with code
`max_turns`, as the budget path always did.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from app.agent.driver import _UsageLedger
from app.agent.events import TokenEvent, UsageEvent
from app.agent.runtime import Turn
from app.models.conversation import RunStatus
from app.schemas.assistant_config import AssistantConfig

pytestmark = pytest.mark.anyio


def _config(max_turns: int) -> AssistantConfig:
    return AssistantConfig.model_validate(
        {"models": {"main": {"model": "claude-sonnet-5", "max_turns": max_turns}}}
    )


class _Looping:
    """A driver whose model keeps calling tools: one usage report per call."""

    name = "looping"

    def __init__(self) -> None:
        self.calls = 0

    async def stream(self, **_: Any) -> Any:
        for i in range(10):
            await asyncio.sleep(0)
            self.calls += 1
            yield UsageEvent(tokens_in=10, tokens_out=5, model_calls=1)
            yield TokenEvent(text=f"step {i} ")


async def _run(monkeypatch: pytest.MonkeyPatch, driver: Any, max_turns: int) -> tuple[Turn, list]:
    monkeypatch.setattr("app.agent.runtime.get_driver", lambda: driver)
    turn = Turn(_config(max_turns), prompt="go")
    events = [e async for e in turn.stream()]
    return turn, events


async def test_the_platform_stops_a_turn_over_the_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    driver = _Looping()
    turn, events = await _run(monkeypatch, driver, max_turns=3)
    errors = [e for e in events if e.type == "error"]
    assert [e.code for e in errors] == ["max_turns"]
    assert "3 model turns" in errors[0].message
    assert (turn.outcome.status, turn.outcome.error) == (
        RunStatus.aborted,
        "maximum agent turns reached",
    )
    assert driver.calls < 10, "the driver was stopped, not drained"


async def test_under_the_limit_nothing_happens(monkeypatch: pytest.MonkeyPatch) -> None:
    turn, events = await _run(monkeypatch, _Looping(), max_turns=20)
    assert not [e for e in events if e.type == "error"]
    assert turn.outcome.status is RunStatus.ok


class _SdkStops:
    """The SDK hit max_turns itself: it says so in the final usage report."""

    name = "sdk"

    async def stream(self, **_: Any) -> Any:
        yield TokenEvent(text="partial ")
        yield UsageEvent(tokens_in=1, tokens_out=1, num_turns=4, terminal_reason="max_turns")


async def test_the_sdks_own_stop_reaches_the_client(monkeypatch: pytest.MonkeyPatch) -> None:
    turn, events = await _run(monkeypatch, _SdkStops(), max_turns=4)
    assert [e.code for e in events if e.type == "error"] == ["max_turns"]
    assert turn.outcome.status is RunStatus.aborted


def test_the_ledger_counts_each_api_response_once() -> None:
    """The CLI can repeat one response's usage across several messages."""
    ledger = _UsageLedger()
    first = ledger.observe("msg_1", "claude-sonnet-5", {"input_tokens": 10, "output_tokens": 2})
    again = ledger.observe("msg_1", "claude-sonnet-5", {"input_tokens": 10, "output_tokens": 9})
    second = ledger.observe("msg_2", "claude-sonnet-5", {"input_tokens": 4, "output_tokens": 1})
    assert first is not None and first.model_calls == 1
    assert again is not None and again.model_calls == 0
    assert second is not None and second.model_calls == 1
