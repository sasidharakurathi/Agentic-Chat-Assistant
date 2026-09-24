"""Stopping a turn on purpose (F-3 / task 1.6).

Interrupt did not exist on either side: no endpoint, no call into the SDK's
own `client.interrupt()`, and the chat UI never passed an abort signal. Now
`POST /conversations/{id}:interrupt` reaches the running turn (on this worker
directly, on others via Redis), the driver is asked to stop cooperatively,
and a driver that ignores it is cancelled after a grace period. Either way
the turn ends *normally*: persisted as aborted, with the partial answer,
and a `done` event.

Like the other mid-turn tests, these drive `run_message` directly — httpx's
ASGITransport buffers the whole response, so a stream cannot be interrupted
through it. The endpoint's own behaviour (who may call it) is tested over
the API.
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Any

import pytest
from app.agent import interrupts
from app.agent.events import TokenEvent, UsageEvent
from app.db.session import get_sessionmaker
from app.models.approval import Approval, ApprovalStatus
from app.models.conversation import Message, MessageRole, Run, RunStatus
from app.services import chat as chat_svc
from httpx import AsyncClient
from sqlalchemy import select
from tests.test_chat_approvals import _assistant_with_db, _await_prompt, _run_turn, fake_sql
from tests.test_chat_approvals import _conversation as _approval_conversation
from tests.test_rbac_escalation import _conversation_of, _team

__all__ = ["fake_sql"]  # a fixture, imported for use by name

pytestmark = pytest.mark.anyio

WORDS = ("one ", "two ", "three ", "four ", "five ", "six ")


class _SlowDriver:
    name = "slow"

    def __init__(self, *, obeys: bool) -> None:
        self.obeys = obeys

    async def stream(self, *, interrupt: asyncio.Event | None = None, **_: Any) -> Any:
        for word in WORDS:
            await asyncio.sleep(0.05)
            if self.obeys and interrupt is not None and interrupt.is_set():
                break
            yield TokenEvent(text=word)
        yield UsageEvent(tokens_in=10, tokens_out=3, cost_usd=0.0)


async def _conversation(client: AsyncClient, headers: dict[str, str]) -> uuid.UUID:
    a = (await client.post("/api/v1/assistants", json={"name": "Echo"}, headers=headers)).json()
    r = await client.post(f"/api/v1/assistants/{a['id']}/conversations", json={}, headers=headers)
    return uuid.UUID(r.json()["id"])


async def _run_and_interrupt(cid: uuid.UUID) -> list[Any]:
    """Stream a turn, and interrupt it the way the endpoint does once the
    first token has arrived."""
    events: list[Any] = []
    async with get_sessionmaker()() as session:
        async for event in chat_svc.run_message(session, conversation_id=cid, text="go"):
            events.append(event)
            if event.type == "token" and len(events) == 1:
                assert interrupts.interrupt_local(cid) == 1
    return events


async def _records(cid: uuid.UUID) -> tuple[Run, Message]:
    async with get_sessionmaker()() as s:
        (run,) = (await s.scalars(select(Run).where(Run.conversation_id == cid))).all()
        (msg,) = (
            await s.scalars(
                select(Message).where(
                    Message.conversation_id == cid, Message.role == MessageRole.assistant
                )
            )
        ).all()
        return run, msg


async def test_an_interrupted_turn_ends_cleanly_with_what_it_had(
    client: AsyncClient, org_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("app.agent.runtime.get_driver", lambda: _SlowDriver(obeys=True))
    cid = await _conversation(client, org_headers)
    events = await _run_and_interrupt(cid)

    types = [e.type for e in events]
    assert types[-1] == "done", "the stream ends normally, not by being cut off"
    assert types.count("token") < len(WORDS), "it genuinely stopped early"
    run, msg = await _records(cid)
    assert (run.status, run.error) == (RunStatus.aborted, "interrupted")
    assert msg.content.startswith("one"), "the partial answer is kept"
    assert run.tokens_in == 10, "a cooperative driver still reports its usage"


async def test_a_driver_that_ignores_the_interrupt_is_stopped_anyway(
    client: AsyncClient, org_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The grace period is the backstop: a driver that never looks at the
    event (or a CLI that does not respond) is cancelled when it runs out."""
    monkeypatch.setattr("app.agent.runtime.get_driver", lambda: _SlowDriver(obeys=False))
    monkeypatch.setattr("app.agent.runtime.INTERRUPT_GRACE_S", 0.12)
    cid = await _conversation(client, org_headers)
    events = await _run_and_interrupt(cid)

    types = [e.type for e in events]
    assert types[-1] == "done"
    assert types.count("token") < len(WORDS)
    run, _ = await _records(cid)
    assert (run.status, run.error) == (RunStatus.aborted, "interrupted")


async def test_a_finished_turn_is_no_longer_interruptible(
    client: AsyncClient, org_headers: dict[str, str]
) -> None:
    """Registration lasts exactly as long as the stream: nothing lingers to
    be "stopped" once the turn is over."""
    cid = await _conversation(client, org_headers)
    async with get_sessionmaker()() as session:
        types = [
            e.type async for e in chat_svc.run_message(session, conversation_id=cid, text="hi")
        ]
    assert types[-1] == "done"
    assert interrupts.interrupt_local(cid) == 0
    run, _ = await _records(cid)
    assert run.status is RunStatus.ok


async def test_the_fake_driver_honours_an_interrupt() -> None:
    from app.agent.driver import FakeDriver
    from app.agent.options import build_runtime_spec
    from app.schemas.assistant_config import default_config

    stop = asyncio.Event()
    stop.set()
    spec = build_runtime_spec(default_config())
    events = [
        e
        async for e in FakeDriver().stream(
            prompt="hello there", spec=spec, policy=default_config().approval_policy, interrupt=stop
        )
    ]
    assert [e.type for e in events] == ["usage"], "no tokens, but usage is still reported"


async def test_stop_while_an_approval_is_pending_is_immediate(
    client: AsyncClient,
    org_headers: dict[str, str],
    fake_sql: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The driver is parked *inside* the approval wait, so it cannot notice
    an interrupt itself. Found live: Stop on a pending approval took exactly
    the grace period (5 s) and ended by force-cancel. The grace is raised
    here so that only the approval wait noticing Stop can pass this."""
    monkeypatch.setattr("app.agent.runtime.INTERRUPT_GRACE_S", 30.0)
    aid = await _assistant_with_db(client, org_headers)
    cid = uuid.UUID(await _approval_conversation(client, org_headers, aid))

    events: list[Any] = []
    turn = asyncio.create_task(_run_turn(str(cid), "sql: DELETE FROM t WHERE id = 1", events))
    prompt = await _await_prompt(events)
    loop = asyncio.get_running_loop()
    stopped_at = loop.time()
    assert interrupts.interrupt_local(cid) == 1
    await asyncio.wait_for(turn, timeout=10)

    assert loop.time() - stopped_at < 2, "Stop waited for the grace period"
    assert fake_sql["ran"] == [], "a statement stopped before approval never runs"
    assert events[-1].type == "done"
    (result,) = [e for e in events if e.type == "tool_result"]
    assert "stopped" in result.output
    run, _ = await _records(cid)
    assert (run.status, run.error) == (RunStatus.aborted, "interrupted")
    async with get_sessionmaker()() as s:
        row = await s.get(Approval, uuid.UUID(prompt.approval_id))
        assert row is not None
        assert row.status is ApprovalStatus.expired, "it leaves the pending list"


# ── the endpoint ─────────────────────────────────────────────


async def test_the_endpoint_accepts_from_the_conversations_creator(client: AsyncClient) -> None:
    t = await _team(client)
    cid = await _conversation_of(client, t, t.member)
    r = await client.post(f"/api/v1/conversations/{cid}:interrupt", headers=t.h(t.member))
    assert r.status_code == 202


async def test_a_teammate_cannot_stop_someone_elses_turn(client: AsyncClient) -> None:
    t = await _team(client)
    cid = await _conversation_of(client, t, t.member)
    r = await client.post(f"/api/v1/conversations/{cid}:interrupt", headers=t.h(t.member2))
    assert r.status_code == 403
