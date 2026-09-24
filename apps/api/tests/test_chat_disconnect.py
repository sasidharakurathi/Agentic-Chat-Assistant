"""A client disconnect must never lose the turn (F-1).

Every persistence step in `chat.run_message` used to sit *after* the streaming
loop with no try/finally. A disconnect interrupts that loop at a `yield`, so
the assistant message, the `Run` row, the `UsageEvent` and the conversation
cost rollup were all skipped: the turn left no record and its spend went
unbilled. A pending approval was left `pending` forever, so a reviewer could
"approve" something that could no longer run.

Two different things interrupt the loop, and both are reproduced here:

- **aclose()** — the SSE route `break`s when it notices the disconnect, and
  closes the generator (GeneratorExit at the yield).
- **anyio cancellation** — Starlette runs the response in an anyio cancel
  scope and cancels it. Anyio scopes are *level-triggered*: every further
  await inside a cancelled scope raises again, so cleanup that awaits the
  database is itself cancelled unless it is shielded.

Like `test_chat_approvals`, these drive `run_message` directly: httpx's
ASGITransport buffers the whole response, so nothing through it can observe
a turn mid-stream.
"""

from __future__ import annotations

import asyncio
import contextlib
import uuid
from typing import Any

import anyio
import pytest
from app.agent.events import TokenEvent
from app.agent.events import UsageEvent as UsageEvent_
from app.db.session import get_sessionmaker
from app.models.approval import Approval, ApprovalStatus
from app.models.conversation import Conversation, Message, MessageRole, Run, RunStatus
from app.models.usage import UsageEvent
from app.services import chat as chat_svc
from httpx import AsyncClient
from sqlalchemy import select
from tests.test_chat_approvals import _assistant_with_db, _await_prompt, _conversation, fake_sql

pytestmark = pytest.mark.anyio

__all__ = ["fake_sql"]  # re-exported fixture


async def _plain_conversation(client: AsyncClient, headers: dict[str, str]) -> uuid.UUID:
    a = (await client.post("/api/v1/assistants", json={"name": "Echo"}, headers=headers)).json()
    return uuid.UUID(await _conversation(client, headers, str(a["id"])))


async def _records(conversation_id: uuid.UUID) -> dict[str, Any]:
    async with get_sessionmaker()() as s:
        msgs = (
            await s.scalars(select(Message).where(Message.conversation_id == conversation_id))
        ).all()
        runs = (await s.scalars(select(Run).where(Run.conversation_id == conversation_id))).all()
        usage = (
            await s.scalars(select(UsageEvent).where(UsageEvent.conversation_id == conversation_id))
        ).all()
        conv = await s.get(Conversation, conversation_id)
        return {
            "assistant": [m for m in msgs if m.role is MessageRole.assistant],
            "runs": list(runs),
            "usage": list(usage),
            "last_message_at": conv.last_message_at if conv else None,
        }


async def test_closing_the_stream_mid_turn_still_records_the_turn(
    client: AsyncClient, org_headers: dict[str, str]
) -> None:
    """The route's `break` on disconnect."""
    cid = await _plain_conversation(client, org_headers)
    seen: list[Any] = []
    async with get_sessionmaker()() as session:
        agen = chat_svc.run_message(session, conversation_id=cid, text="one two three four five")
        async for event in agen:
            seen.append(event)
            if event.type == "token":
                break
        await agen.aclose()

    assert [e.type for e in seen][-1] == "token", "the turn really was cut short"
    rec = await _records(cid)
    (msg,) = rec["assistant"]
    (run,) = rec["runs"]
    assert run.status is RunStatus.aborted
    assert run.error == "client disconnected"
    assert run.message_id == msg.id
    assert msg.content, "the text that had already streamed is kept"
    assert len(rec["usage"]) == 1, "the spend is recorded, not dropped"
    assert rec["last_message_at"] is not None


class _SlowDriver:
    """Streams tokens with real pauses, so the turn is genuinely suspended
    mid-stream when the cancellation arrives. (The fake driver fills the
    queue faster than it is drained, so nothing suspends until the end.)"""

    name = "slow"

    async def stream(self, **_kwargs: Any) -> Any:
        for word in ("one ", "two ", "three ", "four ", "five "):
            await asyncio.sleep(0.05)
            yield TokenEvent(text=word)
        yield UsageEvent_(tokens_in=10, tokens_out=5, cost_usd=0.0)


async def _cancel_after_first_token(cid: uuid.UUID) -> list[Any]:
    seen: list[Any] = []
    with anyio.CancelScope() as scope:
        async with (
            get_sessionmaker()() as session,
            contextlib.aclosing(
                chat_svc.run_message(session, conversation_id=cid, text="go")
            ) as agen,
        ):
            async for event in agen:
                seen.append(event)
                if event.type == "token":
                    scope.cancel()
    assert scope.cancelled_caught
    return seen


async def test_an_anyio_cancellation_mid_stream_records_an_aborted_turn(
    client: AsyncClient, org_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Starlette's disconnect path, landing while the turn is still streaming.
    The scope stays cancelled, so an unshielded commit in the cleanup would
    itself be cancelled and the record lost anyway."""
    monkeypatch.setattr("app.agent.runtime.get_driver", _SlowDriver)
    cid = await _plain_conversation(client, org_headers)
    seen = await _cancel_after_first_token(cid)

    assert [e.type for e in seen] == ["token"]
    rec = await _records(cid)
    (run,) = rec["runs"]
    (msg,) = rec["assistant"]
    assert run.status is RunStatus.aborted
    assert run.error == "client disconnected"
    assert msg.content == "one", "the text that had streamed is kept, not the rest"
    assert len(rec["usage"]) == 1


async def test_a_cancellation_during_the_final_commit_keeps_the_finished_turn(
    client: AsyncClient, org_headers: dict[str, str]
) -> None:
    """The race found while writing the test above. With events already
    queued nothing suspends, so the stream *completes*, and the first await
    the cancellation can land on is the persistence commit itself. Unshielded,
    that lost a finished turn outright."""
    cid = await _plain_conversation(client, org_headers)
    await _cancel_after_first_token(cid)

    rec = await _records(cid)
    (run,) = rec["runs"]
    (msg,) = rec["assistant"]
    assert run.status is RunStatus.ok, "it genuinely finished, and is recorded as such"
    assert msg.content.startswith("(fake driver)")


async def test_a_disconnect_while_waiting_for_approval_expires_it(
    client: AsyncClient, org_headers: dict[str, str], fake_sql: dict[str, Any]
) -> None:
    """Otherwise the row stays `pending` forever: the reload endpoint keeps
    offering it, and approving it records a decision for a statement that
    can no longer run."""
    aid = await _assistant_with_db(client, org_headers)
    cid = uuid.UUID(await _conversation(client, org_headers, aid))

    events: list[Any] = []

    async def consume() -> None:
        async with (
            get_sessionmaker()() as session,
            contextlib.aclosing(
                chat_svc.run_message(session, conversation_id=cid, text="sql: DELETE FROM t")
            ) as agen,
        ):
            async for event in agen:
                events.append(event)

    # The client goes away while the turn is blocked on a human. Cancelled the
    # way Starlette's BaseHTTPMiddleware does it — an anyio task group scope,
    # which re-delivers cancellation on every await — NOT a one-shot
    # `task.cancel()`. An earlier version of this test used the one-shot form
    # and passed while the live server still left the approval pending: every
    # retry propagated into the turn's pump task through `await task`, and cut
    # its approval cleanup short.
    async with anyio.create_task_group() as tg:
        tg.start_soon(consume)
        prompt = await _await_prompt(events)
        tg.cancel_scope.cancel()
    await asyncio.sleep(0.5)  # anything detached would finish here, and must not be needed

    async with get_sessionmaker()() as s:
        row = await s.get(Approval, uuid.UUID(prompt.approval_id))
        assert row is not None
        assert row.status is ApprovalStatus.expired
    assert fake_sql["ran"] == [], "the statement never ran"

    late = await client.post(
        f"/api/v1/approvals/{prompt.approval_id}:resolve",
        json={"decision": "approved"},
        headers=org_headers,
    )
    assert late.status_code == 400
    assert late.json()["error"]["code"] == "approval_not_pending"

    rec = await _records(cid)
    (run,) = rec["runs"]
    assert run.status is RunStatus.aborted


async def test_a_turn_that_finishes_is_still_ok(
    client: AsyncClient, org_headers: dict[str, str]
) -> None:
    """The abort path must not leak into the normal one."""
    cid = await _plain_conversation(client, org_headers)
    async with get_sessionmaker()() as session:
        types = [
            e.type async for e in chat_svc.run_message(session, conversation_id=cid, text="hi")
        ]
    assert types[-1] == "done"
    (run,) = (await _records(cid))["runs"]
    assert run.status is RunStatus.ok
