"""Approval lifecycle (task 3.8): create → announce → wait → resolve."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent import approval_registry as registry
from app.agent.approvals import redact
from app.api.errors import BadRequest, Forbidden, NotFound
from app.db.pagination import PageResult, keyset_page
from app.db.session import get_sessionmaker
from app.logging import get_logger
from app.models.approval import Approval, ApprovalRisk, ApprovalStatus
from app.models.conversation import Conversation
from app.models.enums import MemberRole
from app.observability import metrics
from app.security.access import member_of
from app.services import audit

log = get_logger(__name__)


async def create(
    session: AsyncSession,
    *,
    conversation_id: uuid.UUID,
    org_id: uuid.UUID,
    tool_name: str,
    tool_input: dict[str, Any],
    risk: str,
    rationale: str,
    timeout_s: float = registry.DEFAULT_TIMEOUT_S,
    tool_call_id: str | None = None,
) -> Approval:
    row = Approval(
        conversation_id=conversation_id,
        org_id=org_id,
        tool_name=tool_name,
        tool_call_id=tool_call_id,
        # Stored already-redacted: this row is read back by the UI and shows
        # up in audit exports, so a credential must never land in it.
        input=redact(tool_input),
        risk=ApprovalRisk(risk),
        rationale=rationale[:4000],
        status=ApprovalStatus.pending,
        expires_at=datetime.now(UTC) + timedelta(seconds=timeout_s),
    )
    session.add(row)
    await session.flush()
    await session.commit()
    return row


async def list_pending(
    session: AsyncSession, conversation_id: uuid.UUID, *, limit: int = 50, cursor: str | None = None
) -> PageResult[Approval]:
    return await keyset_page(
        session,
        select(Approval).where(
            Approval.conversation_id == conversation_id,
            Approval.status == ApprovalStatus.pending,
            # Past its expiry it can no longer be decided (Phase 7a.7), so it
            # is not offered, even before the sweep closes it.
            or_(Approval.expires_at.is_(None), Approval.expires_at > datetime.now(UTC)),
        ),
        [Approval.created_at, Approval.id],
        limit=limit,
        cursor=cursor,
        descending=False,
    )


async def get(session: AsyncSession, approval_id: uuid.UUID) -> Approval:
    row = await session.get(Approval, approval_id)
    if row is None:
        raise NotFound("Approval not found")
    return row


async def assert_can_decide(
    session: AsyncSession, approval: Approval, *, user_id: uuid.UUID
) -> None:
    """Who may decide an approval: the person whose conversation it is, or
    an admin of its org (task 6.3).

    This endpoint converts a refusal into a database write, so it is checked
    here rather than inherited from whatever route happened to load the row.
    It used to accept any member of the org: a teammate who can only read a
    conversation could approve the change it was waiting on. Admins still
    decide here although they can no longer post (`require_conversation_starter`,
    Phase 7a.4): plan D17 keeps today's in-chat approval until the executor
    for admin-approved actions exists.

    Someone outside the org gets 404, not 403, so the id confirms nothing;
    a role below the Studio floor is refused (Phase 7a.3).
    """
    member = await member_of(session, approval.org_id, user_id, missing="Approval not found")
    if member.role.satisfies(MemberRole.admin):
        return
    owner = await session.scalar(
        select(Conversation.created_by).where(Conversation.id == approval.conversation_id)
    )
    if owner != user_id:
        raise Forbidden(
            "Only the person in this conversation, or an admin, can approve or decline this.",
            code="not_conversation_owner",
        )


async def resolve(
    session: AsyncSession,
    approval: Approval,
    *,
    decision: str,
    user_id: uuid.UUID | None,
    ip: str | None = None,
) -> Approval:
    """Record a human decision and wake whoever is waiting.

    One conditional UPDATE decides it (Phase 7a.7): only a row that is still
    pending and not past its expiry changes. It used to read the row, then
    write it, so two decisions could both pass the check, and a decision
    arriving just after the turn gave up waiting was recorded as "approved"
    although nothing ran. A second decision is refused rather than
    overwriting the first: the tool has run (or not) by then.
    """
    if decision not in ("approved", "denied"):
        raise BadRequest("decision must be 'approved' or 'denied'", code="invalid_decision")
    now = datetime.now(UTC)
    won = await session.scalar(
        update(Approval)
        .where(
            Approval.id == approval.id,
            Approval.status == ApprovalStatus.pending,
            or_(Approval.expires_at.is_(None), Approval.expires_at > now),
        )
        .values(status=ApprovalStatus(decision), decided_by=user_id, decided_at=now)
        .returning(Approval.id)
        .execution_options(synchronize_session=False)
    )
    if won is None:
        await session.rollback()
        await session.refresh(approval)
        if approval.status is not ApprovalStatus.pending:
            raise BadRequest(
                f"this approval was already {approval.status.value}", code="approval_not_pending"
            )
        raise BadRequest("this approval has expired", code="approval_expired")
    # In the same transaction as the decision: an audit entry exists exactly
    # when a decision was recorded.
    await audit.record(
        session,
        action=f"approval.{decision}",
        org_id=approval.org_id,
        actor_user_id=user_id,
        target_type="approval",
        target_id=approval.id,
        meta={"tool": approval.tool_name, "conversation_id": str(approval.conversation_id)},
        ip=ip,
    )
    await session.commit()
    await session.refresh(approval)
    if approval.created_at is not None and approval.decided_at is not None:
        waited = (approval.decided_at - approval.created_at).total_seconds()
        metrics.APPROVAL_WAIT.observe(max(0.0, waited), decision)

    # Local first (the common case: same worker), then fan out for the case
    # where the waiting turn is on another one.
    if not registry.resolve_local(approval.id, decision):  # type: ignore[arg-type]
        await registry.publish(approval.id, decision)  # type: ignore[arg-type]
    return approval


async def close(approval_id: uuid.UUID) -> ApprovalStatus | None:
    """End a wait that got no answer (it timed out, was stopped, or its turn
    is gone): a still-pending row becomes `expired`. Returns the row's status
    as it now stands, so the turn acts on what was recorded.

    Conditional, like `resolve`: a decision that landed first is never
    overwritten. Its own session, because the turn's session is mid-stream
    and may be rolled back; the record must survive regardless.
    """
    async with get_sessionmaker()() as session:
        now = datetime.now(UTC)
        await session.execute(
            update(Approval)
            .where(Approval.id == approval_id, Approval.status == ApprovalStatus.pending)
            .values(status=ApprovalStatus.expired, decided_at=now)
            .execution_options(synchronize_session=False)
        )
        await session.commit()
        status = await session.scalar(select(Approval.status).where(Approval.id == approval_id))
        return ApprovalStatus(status) if status is not None else None


async def cancel_unrun(approval_id: uuid.UUID) -> None:
    """An approval that was given but whose action will never run, because
    its turn stopped first: recorded as `cancelled`, so the row never claims
    something happened that didn't (Phase 7a.7)."""
    async with get_sessionmaker()() as session:
        await session.execute(
            update(Approval)
            .where(Approval.id == approval_id, Approval.status == ApprovalStatus.approved)
            .values(status=ApprovalStatus.cancelled)
            .execution_options(synchronize_session=False)
        )
        await session.commit()


#: How long past its expiry a pending approval may stay before the sweep
#: closes it: the waiting turn closes its own at expiry, so anything older
#: belongs to a process that died (an OOM kill, a restart mid-wait).
ORPHAN_GRACE_S = 60


async def sweep_orphaned(now: datetime | None = None) -> int:
    """Close approvals a dead process left pending past their expiry, each
    with an audit entry by the system (Phase 7a.7). Before this nothing did,
    so they stayed "pending" forever and could still be offered."""
    cutoff = (now or datetime.now(UTC)) - timedelta(seconds=ORPHAN_GRACE_S)
    async with get_sessionmaker()() as session:
        rows = (
            await session.execute(
                update(Approval)
                .where(Approval.status == ApprovalStatus.pending, Approval.expires_at < cutoff)
                .values(status=ApprovalStatus.expired, decided_at=datetime.now(UTC))
                .returning(
                    Approval.id, Approval.org_id, Approval.conversation_id, Approval.tool_name
                )
                .execution_options(synchronize_session=False)
            )
        ).all()
        for approval_id, org_id, conversation_id, tool in rows:
            await audit.record(
                session,
                action="approval.expired",
                org_id=org_id,
                target_type="approval",
                target_id=approval_id,
                meta={"reason": "orphaned", "tool": tool, "conversation_id": str(conversation_id)},
            )
        await session.commit()
    if rows:
        log.info("approvals_orphaned_closed", count=len(rows))
    return len(rows)


__all__ = [
    "ORPHAN_GRACE_S",
    "assert_can_decide",
    "cancel_unrun",
    "close",
    "create",
    "get",
    "list_pending",
    "resolve",
    "sweep_orphaned",
]
