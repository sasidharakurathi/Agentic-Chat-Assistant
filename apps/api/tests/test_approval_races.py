"""Requests that tell the truth, and recovery after a dead process (7a.7,
durable since 9.1).

Two bugs the gap review found. A decision arriving just after the turn gave
up waiting was recorded as "approved" although nothing ran: resolve read the
row, then wrote it, and expiry only touched rows still pending. And a
process killed mid-wait left its approval "pending" forever, with nothing to
close it, and the question it was answering never got a reply. Since Phase
9.1 nothing waits inside a turn: every move of a request is one conditional
UPDATE, and the sweep owns expiry.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from app import worker
from app.api.errors import BadRequest
from app.db.session import get_sessionmaker
from app.models.approval import Approval, ApprovalStatus
from app.models.audit_log import AuditLog
from app.models.conversation import Conversation, Message, MessageRole
from app.services import approvals as svc
from app.services import chat, executor, recovery, turns
from httpx import AsyncClient
from sqlalchemy import select
from tests.pending import waiting_request

pytestmark = pytest.mark.anyio

API = "/api/v1"


@pytest.fixture(autouse=True)
def _nothing_runs(monkeypatch: pytest.MonkeyPatch) -> None:
    """The subject is the decision, not running it: an approval is left
    `approved`, as if the executor had not picked it up yet."""

    async def dispatch(_approval_id: uuid.UUID) -> None:
        return None

    monkeypatch.setattr(executor, "dispatch", dispatch)


async def _conversation(client: AsyncClient, h: dict[str, str]) -> Conversation:
    aid = (await client.post(f"{API}/assistants", json={"name": "A"}, headers=h)).json()["id"]
    cid = (
        await client.post(
            f"{API}/assistants/{aid}/conversations", json={"mode": "preview"}, headers=h
        )
    ).json()["id"]
    async with get_sessionmaker()() as s:
        conv = await s.get(Conversation, uuid.UUID(cid))
        assert conv is not None
        return conv


async def _approval(conv: Conversation, *, expires_in: float | None = None) -> uuid.UUID:
    async with get_sessionmaker()() as s:
        row = await waiting_request(
            s,
            conversation_id=conv.id,
            tool_input={"sql": "UPDATE tickets SET status = 'closed'"},
            expires_in=expires_in,
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
            await svc.resolve(
                s,
                row,
                decision=decision,
                user_id=user_id,
                input_sha256=row.input_sha256,
                note="Decided in a test.",
            )
        except BadRequest as exc:
            return exc.code
        return "ok"


async def _audit(approval_id: uuid.UUID, action: str | None = None) -> list[AuditLog]:
    async with get_sessionmaker()() as s:
        rows = (
            await s.scalars(select(AuditLog).where(AuditLog.target_id == str(approval_id)))
        ).all()
        return [r for r in rows if action is None or r.action == action]


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
    (entry,) = await _audit(aid, f"approval.{winner}")
    assert entry.actor_user_id == conv.created_by
    loser = "denied" if winner == "approved" else "approved"
    assert await _audit(aid, f"approval.{loser}") == []


async def test_a_decision_after_the_expiry_is_refused(
    client: AsyncClient, org_headers: dict[str, str]
) -> None:
    conv = await _conversation(client, org_headers)
    aid = await _approval(conv, expires_in=-1)
    assert await _decide(aid, "approved", conv.created_by) == "approval_expired"
    assert await _status(aid) is ApprovalStatus.pending, "nothing was recorded"
    assert await _audit(aid, "approval.approved") == []


async def test_a_decision_must_name_what_it_was_shown(
    client: AsyncClient, org_headers: dict[str, str]
) -> None:
    """Phase 9.1: the request's hash comes back with the decision, so an
    approval never covers something the decider didn't read."""
    conv = await _conversation(client, org_headers)
    aid = await _approval(conv)
    r = await client.post(
        f"{API}/approvals/{aid}:resolve",
        json={"decision": "approved", "input_sha256": "0" * 64},
        headers=org_headers,
    )
    assert (r.status_code, r.json()["error"]["code"]) == (400, "input_changed")
    assert await _status(aid) is ApprovalStatus.pending


async def test_the_sweep_never_overwrites_a_decision(
    client: AsyncClient, org_headers: dict[str, str]
) -> None:
    conv = await _conversation(client, org_headers)
    aid = await _approval(conv)
    assert await _decide(aid, "approved", conv.created_by) == "ok"
    later = datetime.now(UTC) + timedelta(days=30)
    await svc.sweep(later)
    assert await _status(aid) is ApprovalStatus.approved
    other = await _approval(conv)
    await svc.sweep(later)
    assert await _status(other) is ApprovalStatus.expired
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


# ── after a dead process ─────────────────────────────────────


async def test_the_sweep_expires_what_nobody_decided_and_says_so(
    client: AsyncClient, org_headers: dict[str, str]
) -> None:
    conv = await _conversation(client, org_headers)
    gone = await _approval(conv, expires_in=-1)
    live = await _approval(conv)
    decided = await _approval(conv, expires_in=-1)
    async with get_sessionmaker()() as s:
        row = await s.get(Approval, decided)
        assert row is not None
        row.status = ApprovalStatus.denied
        await s.commit()

    assert await svc.sweep_orphaned() == 1
    assert await _status(gone) is ApprovalStatus.expired
    assert await _status(live) is ApprovalStatus.pending
    assert await _status(decided) is ApprovalStatus.denied
    (entry,) = await _audit(gone, "approval.expired")
    assert entry.actor_user_id is None
    async with get_sessionmaker()() as s:
        row = await s.get(Approval, gone)
        assert row is not None and row.sealed_ciphertext is None, "the sealed input is wiped"
    notices = [m.content for m in await _replies(conv)]
    assert any("wasn't decided in time" in n for n in notices), "the asker is told"


async def test_a_request_left_running_by_a_dead_process_fails_and_never_reruns(
    client: AsyncClient, org_headers: dict[str, str]
) -> None:
    conv = await _conversation(client, org_headers)
    aid = await _approval(conv)
    async with get_sessionmaker()() as s:
        row = await s.get(Approval, aid)
        assert row is not None
        row.status = ApprovalStatus.executing
        row.executed_at = datetime.now(UTC) - svc.STUCK_AFTER - timedelta(seconds=5)
        await s.commit()
    counts = await svc.sweep()
    assert counts["stuck"] == 1
    assert await _status(aid) is ApprovalStatus.failed
    async with get_sessionmaker()() as s:
        row = await s.get(Approval, aid)
        assert row is not None and "may or may not have happened" in (row.result or "")


async def test_an_approved_request_nobody_picked_up_is_handed_over_again(
    client: AsyncClient, org_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    conv = await _conversation(client, org_headers)
    aid = await _approval(conv)
    assert await _decide(aid, "approved", conv.created_by) == "ok"
    handed: list[uuid.UUID] = []

    async def dispatch(approval_id: uuid.UUID) -> None:
        handed.append(approval_id)

    monkeypatch.setattr(executor, "dispatch", dispatch)
    assert (await svc.sweep())["handed_over"] == 0, "too soon: the executor may be on it"
    async with get_sessionmaker()() as s:
        row = await s.get(Approval, aid)
        assert row is not None
        row.decided_at = datetime.now(UTC) - svc.UNCLAIMED_AFTER - timedelta(seconds=5)
        await s.commit()
    assert (await svc.sweep())["handed_over"] == 1
    assert handed == [aid]


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
