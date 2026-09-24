"""Conversation-level contract gaps (task 1.5).

- `external_user_ref` (plan §2.5): an embedding app tags conversations with its
  own end-user id and lists them back. The column did not exist.
- Tool events carry previews (plan §4.1): a tool result was streamed and
  stored in full whatever its size, and tool inputs were not redacted.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from app.agent.events import TokenEvent, ToolCallEvent, ToolResultEvent, UsageEvent
from app.agent.runtime import TOOL_OUTPUT_PREVIEW_CHARS
from app.db.session import get_sessionmaker
from app.models.conversation import Message, MessageRole
from app.services import chat as chat_svc
from httpx import AsyncClient
from sqlalchemy import select
from tests.test_chat import _new_assistant

pytestmark = pytest.mark.anyio


async def test_conversations_can_be_tagged_and_listed_per_end_user(
    client: AsyncClient, org_headers: dict[str, str]
) -> None:
    aid = await _new_assistant(client, org_headers)
    url = f"/api/v1/assistants/{aid}/conversations"
    alice = await client.post(url, json={"external_user_ref": "user-alice"}, headers=org_headers)
    await client.post(url, json={"external_user_ref": "user-bob"}, headers=org_headers)
    await client.post(url, json={}, headers=org_headers)
    assert alice.json()["external_user_ref"] == "user-alice"

    only = await client.get(url, params={"external_user_ref": "user-alice"}, headers=org_headers)
    assert [c["id"] for c in only.json()["items"]] == [alice.json()["id"]]
    everyone = await client.get(url, headers=org_headers)
    assert len(everyone.json()["items"]) == 3


class _NoisyTool:
    name = "noisy"

    async def stream(self, **_: Any) -> Any:
        yield ToolCallEvent(
            id="t1", name="mcp__caps__http_request", input={"url": "x", "password": "hunter2"}
        )
        yield ToolResultEvent(id="t1", status="success", output="r" * 20_000)
        yield TokenEvent(text="done")
        yield UsageEvent(tokens_in=1, tokens_out=1)


async def test_tool_events_are_previews(
    client: AsyncClient, org_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("app.agent.runtime.get_driver", _NoisyTool)
    aid = await _new_assistant(client, org_headers)
    r = await client.post(f"/api/v1/assistants/{aid}/conversations", json={}, headers=org_headers)
    cid = uuid.UUID(r.json()["id"])
    async with get_sessionmaker()() as session:
        events = [e async for e in chat_svc.run_message(session, conversation_id=cid, text="q")]

    call = next(e for e in events if e.type == "tool_call")
    result = next(e for e in events if e.type == "tool_result")
    assert call.input["password"] == "<redacted>"
    assert result.truncated is True
    assert len(result.output) < TOOL_OUTPUT_PREVIEW_CHARS + 100
    assert "more characters not shown" in result.output

    async with get_sessionmaker()() as s:
        (msg,) = (
            await s.scalars(
                select(Message).where(
                    Message.conversation_id == cid, Message.role == MessageRole.assistant
                )
            )
        ).all()
    (block,) = [b for b in msg.blocks if b.get("type") == "tool_call"]
    assert "hunter2" not in str(block), "the stored copy is redacted too"
    assert len(block["output"]) < TOOL_OUTPUT_PREVIEW_CHARS + 100


def test_a_short_output_is_left_alone() -> None:
    from app.agent.runtime import _preview

    ev = ToolResultEvent(id="t", status="success", output="small")
    assert _preview(ev) == ev
