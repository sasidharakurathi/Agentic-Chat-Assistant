"""The conversation budget is enforced *during* a turn, not after it (F-9).

Two things were wrong. The SDK was handed the assistant's full cap as its own
`max_budget_usd` on every turn, though it knows nothing about what earlier
turns spent — so a conversation with a cent left could spend another full cap
before the SDK stopped it. And cost only arrived in the turn's final usage
event, so our own check fired after the answer had streamed and the money
was gone. Usage is now reported per model call (see `_UsageLedger` in the
driver), and the SDK gets the remainder.
"""

from __future__ import annotations

import asyncio
import uuid
from decimal import Decimal
from typing import Any

import pytest
from app.agent.approvals import build_can_use_tool
from app.agent.events import TokenEvent, UsageEvent
from app.agent.options import RuntimeSpec, build_claude_options, turn_budget
from app.db.session import get_sessionmaker
from app.models.conversation import Conversation
from app.schemas.assistant_config import ApprovalPolicy
from app.services import chat as chat_svc
from httpx import AsyncClient
from tests.test_chat import _new_assistant, _new_conversation

pytestmark = pytest.mark.anyio


@pytest.mark.parametrize(
    ("cap", "remaining", "expected"),
    [
        (None, None, None),  # no cap anywhere
        (1.0, None, 1.0),  # a cap and nothing spent yet
        (1.0, 0.25, 0.25),  # the remainder is tighter
        (None, 0.25, 0.25),
        (0.1, 5.0, 0.1),  # never looser than the configured cap
    ],
)
def test_turn_budget_is_the_tighter_bound(
    cap: float | None, remaining: float | None, expected: float | None
) -> None:
    assert turn_budget(cap, remaining) == expected


class _Recorder:
    """Driver that remembers the spec it was given, and spends as it goes:
    each word is one model call with its own usage report."""

    name = "recorder"
    spec: RuntimeSpec | None = None

    def __init__(self, words: int = 1, cost_per_word: float = 0.0) -> None:
        self.words = words
        self.cost_per_word = cost_per_word
        self.emitted = 0

    async def stream(self, *, spec: RuntimeSpec, **_: Any) -> Any:
        _Recorder.spec = spec
        for i in range(self.words):
            await asyncio.sleep(0)
            self.emitted += 1
            yield TokenEvent(text=f"w{i} ")
            yield UsageEvent(tokens_in=1, tokens_out=1, cost_usd=self.cost_per_word)


async def _with_spend(
    client: AsyncClient, headers: dict[str, str], *, cap: float, spent: float
) -> uuid.UUID:
    aid = await _new_assistant(
        client, headers, {"models": {"main": {"model": "claude-sonnet-5", "max_budget_usd": cap}}}
    )
    cid = uuid.UUID(await _new_conversation(client, headers, aid))
    async with get_sessionmaker()() as s:
        conv = await s.get(Conversation, cid)
        assert conv is not None
        conv.cost_usd = Decimal(str(spent))
        await s.commit()
    return cid


async def _run(cid: uuid.UUID) -> list[Any]:
    async with get_sessionmaker()() as session:
        return [e async for e in chat_svc.run_message(session, conversation_id=cid, text="go")]


async def test_the_sdk_is_given_what_is_left_not_the_whole_cap(
    client: AsyncClient, org_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("app.agent.runtime.get_driver", _Recorder)
    cid = await _with_spend(client, org_headers, cap=1.0, spent=0.75)
    await _run(cid)

    spec = _Recorder.spec
    assert spec is not None
    assert spec.max_budget_usd == pytest.approx(0.25)
    # ...and that is the number the real SDK actually receives.
    assert build_claude_options(
        spec, build_can_use_tool(ApprovalPolicy())
    ).max_budget_usd == pytest.approx(0.25)


async def test_a_turn_stops_when_the_money_runs_out_not_when_it_ends(
    client: AsyncClient, org_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """$0.30 left and each model call costs $0.10: the turn must stop at the
    third call, not stream all ten and discover the overspend afterwards."""
    driver = _Recorder(words=10, cost_per_word=0.10)
    monkeypatch.setattr("app.agent.runtime.get_driver", lambda: driver)
    cid = await _with_spend(client, org_headers, cap=1.0, spent=0.70)
    events = await _run(cid)

    tokens = [e.text for e in events if e.type == "token"]
    assert tokens == ["w0 ", "w1 ", "w2 "]
    assert any(e.type == "error" and e.code == "budget_exceeded" for e in events)
    assert driver.emitted < 10, "the driver was stopped, not drained"
