"""Separation of duties (Phase 7b.3).

An approval is a second pair of eyes; one person approving their own work
makes the audit log claim a review that never happened.

- **Assistant versions.** Whoever submitted a version, or built the
  assistant, may not approve it while another active admin or the owner
  could. With nobody else to ask (a one-person organisation, as a small
  install often is) the owner may, only with a written note, and the audit
  entry says it was self-approved.
- **Tool calls.** Plan D17 keeps today's in-chat approval, decided by the
  person in the conversation, until Phase 9 brings durable approvals an
  admin can answer later; requiring someone else within today's five-minute
  wait would expire nearly every write. Until then each decision records
  who asked, who decided, their role and whether it was the same person
  (`approvals.resolve`), so the record never claims more than happened.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.errors import Forbidden
from app.models.enums import MemberRole
from app.models.membership import Membership
from app.models.user import User
from app.services import org_settings

#: Roles that can approve an assistant version.
REVIEWERS = (MemberRole.admin, MemberRole.owner)


@dataclass(frozen=True)
class Review:
    """How an approval may go ahead."""

    #: The approver is approving their own work, allowed only as the
    #: owner with nobody else to ask.
    self_approved: bool


async def other_reviewers(
    session: AsyncSession, org_id: uuid.UUID, excluding: set[uuid.UUID]
) -> int:
    """Active admins and owners of the org, other than `excluding`."""
    count = await session.scalar(
        select(func.count())
        .select_from(Membership)
        .join(User, User.id == Membership.user_id)
        .where(
            Membership.org_id == org_id,
            Membership.role.in_(REVIEWERS),
            User.is_active.is_(True),
            Membership.deactivated_at.is_(None),
            Membership.user_id.not_in(excluding),
        )
    )
    return int(count or 0)


async def may_approve_version(
    session: AsyncSession,
    *,
    org_id: uuid.UUID,
    approver_id: uuid.UUID,
    approver_role: MemberRole,
    submitted_by: uuid.UUID | None,
    built_by: uuid.UUID | None,
    note: str,
) -> Review:
    """Refuse an approver who submitted the version or built the assistant,
    unless they are the owner and nobody else can review; then a note is
    required. Returns how it may go ahead."""
    involved = {u for u in (submitted_by, built_by) if u is not None}
    if approver_id not in involved:
        return Review(self_approved=False)
    if await other_reviewers(session, org_id, involved | {approver_id}) > 0:
        raise Forbidden(
            "You submitted this version or built this assistant, so another admin or the "
            "owner has to approve it",
            code="cannot_approve_own",
        )
    if approver_role is not MemberRole.owner:
        raise Forbidden(
            "Nobody else can review this yet: the owner has to approve it",
            code="cannot_approve_own",
        )
    if not (await org_settings.switches(session, org_id)).sole_reviewer_self_approval:
        raise Forbidden(
            "This organisation needs someone else to review your work. Make another "
            "member an admin, or switch on sole-reviewer approval in Settings",
            code="cannot_approve_own",
        )
    if not note.strip():
        raise Forbidden(
            "You are approving your own work because nobody else can review it. "
            "Say why it is ready in the note: it is kept with the approval",
            code="self_approval_needs_note",
        )
    return Review(self_approved=True)


__all__ = ["REVIEWERS", "Review", "may_approve_version", "other_reviewers"]
