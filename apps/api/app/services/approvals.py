"""Approval lifecycle (task 3.8): create → announce → wait → resolve."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
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
from app.models.membership import Membership
from app.observability import metrics

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
    conversation could approve the change it was waiting on. That is the
    same rule as posting into a conversation (`require_conversation_editor`).

    Someone outside the org gets 404, not 403, so the id confirms nothing.
    """
    member = await session.scalar(
        select(Membership).where(
            Membership.org_id == approval.org_id, Membership.user_id == user_id
        )
    )
    if member is None:
        raise NotFound("Approval not found")
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
) -> Approval:
    """Record a human decision and wake whoever is waiting.

    Rejects a second decision rather than overwriting the first: the tool has
    already run (or not) by then, so accepting it would record something that
    never happened.
    """
    if approval.status is not ApprovalStatus.pending:
        raise BadRequest(
            f"this approval was already {approval.status.value}", code="approval_not_pending"
        )
    if decision not in ("approved", "denied"):
        raise BadRequest("decision must be 'approved' or 'denied'", code="invalid_decision")

    approval.status = ApprovalStatus(decision)
    approval.decided_by = user_id
    approval.decided_at = datetime.now(UTC)
    await session.flush()
    await session.commit()
    if approval.created_at is not None:
        waited = (approval.decided_at - approval.created_at).total_seconds()
        metrics.APPROVAL_WAIT.observe(max(0.0, waited), decision)

    # Local first (the common case: same worker), then fan out for the case
    # where the waiting turn is on another one.
    if not registry.resolve_local(approval.id, decision):  # type: ignore[arg-type]
        await registry.publish(approval.id, decision)  # type: ignore[arg-type]
    return approval


async def expire(approval_id: uuid.UUID) -> None:
    """Mark a timed-out approval, in its own session.

    Separate session because the turn's session is mid-stream and may be
    rolled back; the record that nobody answered should survive regardless.
    """
    sessionmaker = get_sessionmaker()
    async with sessionmaker() as session:
        row = await session.get(Approval, approval_id)
        if row is not None and row.status is ApprovalStatus.pending:
            row.status = ApprovalStatus.expired
            row.decided_at = datetime.now(UTC)
            await session.commit()


__all__ = ["assert_can_decide", "create", "expire", "get", "list_pending", "resolve"]
