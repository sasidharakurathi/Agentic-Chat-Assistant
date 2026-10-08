"""Approvals that tell the truth, and recovery after a dead process (7a.7).

Two bugs the gap review found. A decision arriving just after the turn gave
up waiting was recorded as "approved" although nothing ran: resolve read the
row, then wrote it, and expiry only touched rows still pending. And a
process killed mid-wait left its approval "pending" forever, with nothing to
close it, and the question it was answering never got a reply.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from app import worker
from app.agent import approval_registry
from app.api.errors import BadRequest
from app.db.session import get_sessionmaker
from app.models.approval import Approval, ApprovalStatus
from app.models.audit_log import AuditLog
from app.models.conversation import Conversation, Message, MessageRole
from app.services import approvals as svc
from app.services import chat, recovery, turns
from httpx import AsyncClient
from sqlalchemy import select

pytestmark = pytest.mark.anyio

API = "/api/v1"


async def _conversation(client: AsyncClient, h: dict[str, str]) -> Conversation:
    aid = (await client.post(f"{API}/assistants", json={"name": "A"}, headers=h)).json()["id"]
    cid = (await client.post(f"{API}/assistants/{aid}/conversations", json={}, headers=h)).json()[
        "id"
    ]
    async with get_sessionmaker()() as s:
        conv = await s.get(Conversation, uuid.UUID(cid))
        assert conv is not None
        return conv


async def _approval(conv: Conversation, *, expires_in: float = 300) -> uuid.UUID:
    async with get_sessionmaker()() as s:
        row = await svc.create(
            s,
            conversation_id=conv.id,
            org_id=conv.org_id,
            tool_name="mcp__caps__sql_query",
            tool_input={"sql": "UPDATE tickets SET status = 'closed'"},
            risk="high",
            rationale="writes",
            timeout_s=expires_in,
        )
        return row.id


async def _status(approval_id: uuid.UUID) -> ApprovalStatus:
    async with get_sessionmaker()() as s:
        row = await s.get(Approval, approval_id)
        assert row is not None
        return row.status


async def _decide(approval_id: uuid.UUID, decision: str, user_id: uuid.UUID | None) -> str:
    """The resolve endpoint's work, in its own session: 'ok' or the refusal."""
    async with get_sessionmaker()() as s:
        row = await svc.get(s, approval_id)
        try:
            await svc.resolve(s, row, decision=decision, user_id=user_id)
        except BadRequest as exc:
            return exc.code
        return "ok"


async def _audit(approval_id: uuid.UUID) -> list[AuditLog]:
    async with get_sessionmaker()() as s:
        return list(
            (await s.scalars(select(AuditLog).where(AuditLog.target_id == str(approval_id)))).all()
        )


# ── deciding ─────────────────────────────────────────────────


async def test_two_opposite_decisions_at_once_record_exactly_one(
    client: AsyncClient, org_headers: dict[str, str]
) -> None:
    conv = await _conversation(client, org_headers)
    aid = await _approval(conv)
    results = await asyncio.gather(
        _decide(aid, "approved", conv.created_by), _decide(aid, "denied", conv.created_by)
    )
    assert sorted(results) == ["approval_not_pending", "ok"]
    winner = "approved" if results[0] == "ok" else "denied"
    assert await _status(aid) == ApprovalStatus(winner)
    (entry,) = await _audit(aid)
    assert entry.action == f"approval.{winner}" and entry.actor_user_id == conv.created_by


async def test_a_decision_after_the_expiry_is_refused(
    client: AsyncClient, org_headers: dict[str, str]
) -> None:
    conv = await _conversation(client, org_headers)
    aid = await _approval(conv, expires_in=-1)
    assert await _decide(aid, "approved", conv.created_by) == "approval_expired"
    assert await _status(aid) is ApprovalStatus.pending, "nothing was recorded"
    assert await _audit(aid) == []
    r = await client.post(
        f"{API}/approvals/{aid}:resolve", json={"decision": "approved"}, headers=org_headers
    )
    assert (r.status_code, r.json()["error"]["code"]) == (400, "approval_expired")


async def test_closing_never_overwrites_a_decision(
    client: AsyncClient, org_headers: dict[str, str]
) -> None:
    conv = await _conversation(client, org_headers)
    aid = await _approval(conv)
    assert await _decide(aid, "approved", conv.created_by) == "ok"
    assert await svc.close(aid) is ApprovalStatus.approved
    assert await _status(aid) is ApprovalStatus.approved
    other = await _approval(conv)
    assert await svc.close(other) is ApprovalStatus.expired
    assert await _decide(other, "approved", conv.created_by) == "approval_not_pending"


async def test_an_expired_row_is_not_offered(
    client: AsyncClient, org_headers: dict[str, str]
) -> None:
    conv = await _conversation(client, org_headers)
    live, stale = await _approval(conv), await _approval(conv, expires_in=-1)
    pending = (
        await client.get(f"{API}/conversations/{conv.id}/approvals", headers=org_headers)
    ).json()["items"]
    assert [p["id"] for p in pending] == [str(live)]
    assert str(stale) not in str(pending)


# ── the waiting turn follows what was recorded ───────────────


async def _raise(conv: Conversation, monkeypatch: pytest.MonkeyPatch, outcome: Any) -> str:
    """Run the turn's approval step with the wait replaced by `outcome`, an
    async function given the approval id that returns what the wait would."""

    async def wait(approval_id: Any, _future: Any, **_kw: Any) -> Any:
        try:
            return await outcome(uuid.UUID(str(approval_id)))
        finally:
            # As the real wait does: its slot never outlives it.
            approval_registry.forget(approval_id)

    monkeypatch.setattr(approval_registry, "wait", wait)
    return await chat._raise_approval(
        conv, lambda _e: None, "mcp__caps__sql_query", {"sql": "UPDATE t SET x = 1"}, "high", "w"
    )


async def _only_approval(conv: Conversation) -> uuid.UUID:
    async with get_sessionmaker()() as s:
        (row,) = (
            await s.scalars(select(Approval).where(Approval.conversation_id == conv.id))
        ).all()
        return row.id


async def test_an_approval_that_lands_as_the_wait_times_out_runs(
    client: AsyncClient, org_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The person said yes before the expiry; the wake-up lost the race with
    the timeout. The action runs, and the row says approved: both true."""
    conv = await _conversation(client, org_headers)

    async def approved_then_timed_out(aid: uuid.UUID) -> str:
        assert await _decide(aid, "approved", conv.created_by) == "ok"
        return "expired"

    assert await _raise(conv, monkeypatch, approved_then_timed_out) == "approved"
    assert await _status(await _only_approval(conv)) is ApprovalStatus.approved


async def test_a_denial_that_lands_as_the_wait_times_out_is_a_denial(
    client: AsyncClient, org_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    conv = await _conversation(client, org_headers)

    async def denied_then_timed_out(aid: uuid.UUID) -> str:
        assert await _decide(aid, "denied", conv.created_by) == "ok"
        return "expired"

    assert await _raise(conv, monkeypatch, denied_then_timed_out) == "denied"
    assert await _status(await _only_approval(conv)) is ApprovalStatus.denied


async def test_stop_at_the_moment_of_approval_records_that_nothing_ran(
    client: AsyncClient, org_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    conv = await _conversation(client, org_headers)

    async def approved_then_stopped(aid: uuid.UUID) -> str:
        assert await _decide(aid, "approved", conv.created_by) == "ok"
        return "interrupted"

    assert await _raise(conv, monkeypatch, approved_then_stopped) == "interrupted"
    assert await _status(await _only_approval(conv)) is ApprovalStatus.cancelled


async def test_a_turn_that_vanishes_after_an_approval_records_that_nothing_ran(
    client: AsyncClient, org_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    conv = await _conversation(client, org_headers)

    async def approved_then_gone(aid: uuid.UUID) -> str:
        assert await _decide(aid, "approved", conv.created_by) == "ok"
        raise asyncio.CancelledError

    with pytest.raises(asyncio.CancelledError):
        await _raise(conv, monkeypatch, approved_then_gone)
    assert await _status(await _only_approval(conv)) is ApprovalStatus.cancelled


async def test_an_unanswered_wait_is_closed_as_expired(
    client: AsyncClient, org_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    conv = await _conversation(client, org_headers)

    async def nobody(_aid: uuid.UUID) -> str:
        return "expired"

    assert await _raise(conv, monkeypatch, nobody) == "expired"
    assert await _status(await _only_approval(conv)) is ApprovalStatus.expired


# ── after a dead process ─────────────────────────────────────


async def test_the_sweep_closes_approvals_a_dead_process_left_pending(
    client: AsyncClient, org_headers: dict[str, str]
) -> None:
    conv = await _conversation(client, org_headers)
    orphan = await _approval(conv, expires_in=-(svc.ORPHAN_GRACE_S + 5))
    just_expired = await _approval(conv, expires_in=-1)  # its turn closes it itself
    live = await _approval(conv)
    decided = await _approval(conv, expires_in=-(svc.ORPHAN_GRACE_S + 5))
    async with get_sessionmaker()() as s:
        row = await s.get(Approval, decided)
        assert row is not None
        row.status = ApprovalStatus.denied
        await s.commit()

    assert await svc.sweep_orphaned() == 1
    assert await _status(orphan) is ApprovalStatus.expired
    assert await _status(just_expired) is ApprovalStatus.pending
    assert await _status(live) is ApprovalStatus.pending
    assert await _status(decided) is ApprovalStatus.denied
    (entry,) = await _audit(orphan)
    assert entry.action == "approval.expired" and entry.actor_user_id is None
    assert (entry.meta or {}).get("reason") == "orphaned"


async def _question(conv: Conversation, *, age_s: float) -> uuid.UUID:
    async with get_sessionmaker()() as s:
        m = Message(
            conversation_id=conv.id,
            org_id=conv.org_id,
            role=MessageRole.user,
            content="Why is my laptop slow?",
            created_at=datetime.now(UTC) - timedelta(seconds=age_s),
        )
        s.add(m)
        await s.commit()
        return m.id


async def _replies(conv: Conversation) -> list[Message]:
    async with get_sessionmaker()() as s:
        return list(
            (
                await s.scalars(
                    select(Message).where(
                        Message.conversation_id == conv.id, Message.role == MessageRole.system
                    )
                )
            ).all()
        )


async def test_a_question_a_dead_turn_left_gets_a_notice(
    client: AsyncClient, org_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    dropped = await _conversation(client, org_headers)
    running = await _conversation(client, org_headers)
    fresh = await _conversation(client, org_headers)
    old = recovery.UNANSWERED_AFTER_S + 30
    question = await _question(dropped, age_s=old)
    await _question(running, age_s=old)
    await _question(fresh, age_s=10)

    async def live(ids: list[uuid.UUID]) -> set[uuid.UUID]:
        return {running.id} & set(ids)

    monkeypatch.setattr(turns, "live_anywhere", live)
    assert await recovery.notice_unanswered() == 1
    (notice,) = await _replies(dropped)
    assert notice.content == recovery.INTERRUPTED_NOTICE and notice.parent_id == question
    assert await _replies(running) == [], "a turn is still answering it"
    assert await _replies(fresh) == [], "too recent to call"
    assert await recovery.notice_unanswered() == 0, "only once"


async def test_nothing_is_noticed_when_running_turns_cannot_be_seen(
    client: AsyncClient, org_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """No shared turn log (or Redis down): a turn in another process may be
    answering, so nothing is said."""
    conv = await _conversation(client, org_headers)
    await _question(conv, age_s=recovery.UNANSWERED_AFTER_S + 30)
    assert await turns.live_anywhere([conv.id]) is None, "tests run without a shared log"
    assert await recovery.notice_unanswered() == 0
    assert await _replies(conv) == []


async def test_archived_and_eval_conversations_are_left_alone(
    client: AsyncClient, org_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    archived = await _conversation(client, org_headers)
    await _question(archived, age_s=recovery.UNANSWERED_AFTER_S + 30)
    async with get_sessionmaker()() as s:
        row = await s.get(Conversation, archived.id)
        assert row is not None
        await chat.archive(s, row)

    async def none_live(_ids: list[uuid.UUID]) -> set[uuid.UUID]:
        return set()

    monkeypatch.setattr(turns, "live_anywhere", none_live)
    assert await recovery.notice_unanswered() == 0


def test_the_recovery_sweep_runs_every_minute_from_the_start() -> None:
    (job,) = [j for j in worker.WorkerSettings.cron_jobs if "recovery_sweep" in j.name]
    assert job.run_at_startup is True
    assert job.minute is None, "every minute"


async def test_the_pending_metric_counts_only_what_can_still_be_decided(
    client: AsyncClient, org_headers: dict[str, str]
) -> None:
    from test_metrics import _values  # type: ignore[import-not-found]

    conv = await _conversation(client, org_headers)
    await _approval(conv)
    await _approval(conv, expires_in=-1)
    got = _values((await client.get("/metrics")).text)
    assert got["assistant_studio_approvals_pending"] == 1
