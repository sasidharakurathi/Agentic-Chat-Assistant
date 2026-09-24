"""Bounded turn concurrency and CLI settings (task 1.4).

Each turn runs a Claude CLI subprocess. `AGENT_MAX_CONCURRENCY` was declared
and read by nothing, so a burst of messages was a burst of processes. Now a
turn waits for a slot up to `AGENT_QUEUE_WAIT_S` and is otherwise refused
with a clear `busy` error, and a slot is always given back, including when the
client disconnects mid-turn.
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Any

import pytest
from app.agent.approvals import build_can_use_tool
from app.agent.events import TokenEvent, UsageEvent
from app.agent.options import build_claude_options, build_runtime_spec
from app.db.session import get_sessionmaker
from app.schemas.assistant_config import ApprovalPolicy, default_config
from app.services import chat as chat_svc
from httpx import AsyncClient
from tests.test_chat import _new_assistant, _new_conversation

pytestmark = pytest.mark.anyio


class _Gate:
    """A driver that holds its turn open until released."""

    name = "gate"

    def __init__(self) -> None:
        self.release = asyncio.Event()
        self.started = asyncio.Event()

    async def stream(self, **_: Any) -> Any:
        self.started.set()
        await self.release.wait()
        yield TokenEvent(text="ok")
        yield UsageEvent(tokens_in=1, tokens_out=1)


async def _types(cid: uuid.UUID) -> list[str]:
    async with get_sessionmaker()() as session:
        return [e.type async for e in chat_svc.run_message(session, conversation_id=cid, text="x")]


async def _setup(
    client: AsyncClient, headers: dict[str, str], monkeypatch: pytest.MonkeyPatch, gate: _Gate
) -> tuple[uuid.UUID, uuid.UUID]:
    monkeypatch.setattr("app.services.chat.settings.agent_max_concurrency", 1)
    monkeypatch.setattr("app.services.chat.settings.agent_queue_wait_s", 0.3)
    monkeypatch.setattr("app.services.chat._slots", {})
    monkeypatch.setattr("app.agent.runtime.get_driver", lambda: gate)
    aid = await _new_assistant(client, headers)
    a = uuid.UUID(await _new_conversation(client, headers, aid))
    b = uuid.UUID(await _new_conversation(client, headers, aid))
    return a, b


async def test_a_turn_beyond_the_limit_is_told_it_is_busy(
    client: AsyncClient, org_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    gate = _Gate()
    a, b = await _setup(client, org_headers, monkeypatch, gate)
    first = asyncio.create_task(_types(a))
    await asyncio.wait_for(gate.started.wait(), 5)

    assert await _types(b) == ["error"]

    gate.release.set()
    assert (await first)[-1] == "done"
    assert (await _types(b))[-1] == "done", "the slot came back"


async def test_a_disconnect_gives_the_slot_back(
    client: AsyncClient, org_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    gate = _Gate()
    a, b = await _setup(client, org_headers, monkeypatch, gate)
    async with get_sessionmaker()() as session:
        stream = chat_svc.run_message(session, conversation_id=a, text="x")
        waiting = asyncio.create_task(stream.__anext__())
        await asyncio.wait_for(gate.started.wait(), 5)
        waiting.cancel()  # what a dropped SSE connection does to the turn
        with pytest.raises(asyncio.CancelledError):
            await waiting
        await stream.aclose()

    gate.release.set()
    assert (await _types(b))[-1] == "done"


def test_the_cli_gets_a_timeout_and_no_telemetry() -> None:
    spec = build_runtime_spec(default_config())
    env = build_claude_options(spec, build_can_use_tool(ApprovalPolicy())).env
    assert env["API_TIMEOUT_MS"].isdigit()
    assert env["CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC"] == "1"
    assert env["DISABLE_TELEMETRY"] == "1"
