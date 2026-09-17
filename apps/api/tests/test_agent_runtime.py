"""Unit tests for ``Turn._absorb`` — the bits that don't need a driver."""

from __future__ import annotations

from app.agent.events import ErrorEvent, UsageEvent
from app.agent.runtime import Turn
from app.models.conversation import RunStatus
from app.schemas.assistant_config import default_config


def _turn() -> Turn:
    return Turn(default_config(), prompt="hi")


def test_num_turns_is_taken_from_the_usage_event() -> None:
    turn = _turn()
    turn._absorb(UsageEvent(cost_usd=0.01, num_turns=7))
    assert turn.outcome.num_turns == 7


def test_max_turns_terminal_reason_aborts_the_run() -> None:
    turn = _turn()
    turn._absorb(UsageEvent(cost_usd=0.01, num_turns=24, terminal_reason="max_turns"))
    assert turn.outcome.status == RunStatus.aborted
    assert turn.outcome.error == "maximum agent turns reached"


def test_normal_completion_does_not_abort() -> None:
    turn = _turn()
    turn._absorb(UsageEvent(cost_usd=0.01, num_turns=3, terminal_reason="completed"))
    assert turn.outcome.status == RunStatus.ok


def test_error_event_still_marks_error() -> None:
    turn = _turn()
    turn._absorb(ErrorEvent(code="agent_error", message="boom"))
    assert turn.outcome.status == RunStatus.error
    assert turn.outcome.error == "boom"
