"""Keeping and erasing conversations (Phase 8.10): how long they are kept,
erasing one person, legal holds, and the record of what was erased."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Path
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import select

from app.api.deps import ClientIP, SessionDep, require_role
from app.models.enums import MemberRole
from app.models.membership import Membership
from app.models.retention import LegalHold
from app.models.user import User
from app.schemas.common import ApiModel
from app.services import org_settings, retention

router = APIRouter(tags=["retention"])

RequireAdmin = Depends(require_role(MemberRole.admin))
RequireOwner = Depends(require_role(MemberRole.owner))


class RetentionIn(BaseModel):
    days: int = Field(ge=retention.DAYS_MIN, le=retention.DAYS_MAX)


class RetentionOut(ApiModel):
    days: int
    #: Below India's DPDP one-year floor for logs: allowed, the company's call.
    below_floor: bool


class EraseIn(BaseModel):
    #: The person's email, typed to confirm.
    confirm_email: EmailStr


class ErasedOut(ApiModel):
    conversations: int
    #: Under a hold, or answering right then: not erased.
    kept: int
    memories: int
    comments: int


class HoldIn(BaseModel):
    user_id: uuid.UUID | None = None
    conversation_id: uuid.UUID | None = None
    reason: str = Field(min_length=3, max_length=500)


class HoldRelease(BaseModel):
    hold_id: uuid.UUID


class HoldOut(ApiModel):
    id: uuid.UUID
    user_id: uuid.UUID | None = None
    #: The person held, as they are known in the org.
    user_email: str | None = None
    conversation_id: uuid.UUID | None = None
    reason: str
    created_at: datetime
    released_at: datetime | None = None


class ErasureOut(ApiModel):
    """What was erased, and when: counts, never content."""

    kind: str
    user_id: uuid.UUID | None = None
    user_email: str | None = None
    cutoff: datetime | None = None
    conversations: int
    memories: int
    comments: int
    #: What went with the conversations (Phase 10.5): messages, traces,
    #: tool_calls, summaries, share_links, feedback, review_items,
    #: conversation_memories, screenshots. Missing on older records.
    details: dict[str, int] = Field(default_factory=dict)
    created_at: datetime


async def _emails(session: SessionDep, ids: set[uuid.UUID | None]) -> dict[uuid.UUID, str]:
    wanted = {i for i in ids if i is not None}
    if not wanted:
        return {}
    rows = await session.execute(select(User.id, User.email).where(User.id.in_(wanted)))
    return dict(rows.tuples().all())


def _hold_out(row: LegalHold, emails: dict[uuid.UUID, str]) -> HoldOut:
    return HoldOut(
        id=row.id,
        user_id=row.user_id,
        user_email=emails.get(row.user_id) if row.user_id else None,
        conversation_id=row.conversation_id,
        reason=row.reason,
        created_at=row.created_at,
        released_at=row.released_at,
    )


@router.put("/orgs/{org_id}/retention", response_model=RetentionOut)
async def set_retention(
    org_id: Annotated[uuid.UUID, Path()],
    body: RetentionIn,
    actor: Annotated[Membership, RequireAdmin],
    session: SessionDep,
    ip: ClientIP,
) -> RetentionOut:
    """How many days a quiet conversation is kept before it is erased for
    good, every night. Audited."""
    org = await org_settings.set_retention(
        session, org_id=org_id, days=body.days, actor_id=actor.user_id, ip=ip
    )
    days = org_settings.switches_of(org).retention_days
    return RetentionOut(days=days, below_floor=days < retention.DPDP_FLOOR)


@router.post("/orgs/{org_id}/members/{user_id}:erase", response_model=ErasedOut)
async def erase_member(
    org_id: Annotated[uuid.UUID, Path()],
    user_id: Annotated[uuid.UUID, Path()],
    body: EraseIn,
    actor: Annotated[Membership, RequireOwner],
    session: SessionDep,
    ip: ClientIP,
) -> ErasedOut:
    """Erase one person's data for good: their conversations, what the
    assistants remember about them, their feedback comments. The owner's,
    with their email typed to confirm. Their account and the audit log
    stay. Refused while they are under a legal hold."""
    out = await retention.erase_person(
        session,
        org_id=org_id,
        user_id=user_id,
        confirm_email=str(body.confirm_email),
        actor_id=actor.user_id,
        ip=ip,
    )
    return ErasedOut(
        conversations=out.conversations, kept=out.kept, memories=out.memories, comments=out.comments
    )


@router.get("/orgs/{org_id}/holds", response_model=list[HoldOut])
async def list_holds(
    org_id: Annotated[uuid.UUID, Path()],
    _actor: Annotated[Membership, RequireAdmin],
    session: SessionDep,
) -> list[HoldOut]:
    """Legal holds: those in force first."""
    rows = await retention.holds(session, org_id)
    emails = await _emails(session, {r.user_id for r in rows})
    return [_hold_out(r, emails) for r in rows]


@router.post("/orgs/{org_id}/holds", response_model=HoldOut, status_code=201)
async def create_hold(
    org_id: Annotated[uuid.UUID, Path()],
    body: HoldIn,
    actor: Annotated[Membership, RequireOwner],
    session: SessionDep,
    ip: ClientIP,
) -> HoldOut:
    """Keep a person's conversations, or one conversation, out of retention
    and erasure until the hold is lifted. The owner's; audited."""
    row = await retention.hold(
        session,
        org_id=org_id,
        user_id=body.user_id,
        conversation_id=body.conversation_id,
        reason=body.reason,
        actor_id=actor.user_id,
        ip=ip,
    )
    return _hold_out(row, await _emails(session, {row.user_id}))


@router.post("/orgs/{org_id}/holds:release", response_model=HoldOut)
async def release_hold(
    org_id: Annotated[uuid.UUID, Path()],
    body: HoldRelease,
    actor: Annotated[Membership, RequireOwner],
    session: SessionDep,
    ip: ClientIP,
) -> HoldOut:
    """Lift a hold: retention and erasure apply again. Audited."""
    row = await retention.release(
        session, org_id=org_id, hold_id=body.hold_id, actor_id=actor.user_id, ip=ip
    )
    return _hold_out(row, await _emails(session, {row.user_id}))


@router.get("/orgs/{org_id}/erasures", response_model=list[ErasureOut])
async def list_erasures(
    org_id: Annotated[uuid.UUID, Path()],
    _actor: Annotated[Membership, RequireAdmin],
    session: SessionDep,
) -> list[ErasureOut]:
    """What was erased for good, newest first: counts only."""
    rows = await retention.erasures(session, org_id)
    emails = await _emails(session, {r.user_id for r in rows})
    return [
        ErasureOut(
            kind=r.kind,
            user_id=r.user_id,
            user_email=emails.get(r.user_id) if r.user_id else None,
            cutoff=r.cutoff,
            conversations=len(r.conversation_ids or []),
            memories=r.memories,
            comments=r.comments,
            details={k: int(v) for k, v in (r.details or {}).items()},
            created_at=r.created_at,
        )
        for r in rows
    ]


__all__ = ["router"]
