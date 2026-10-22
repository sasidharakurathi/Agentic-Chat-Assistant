"""Who is told what by email (Phase 7b.5). Each message only notifies,
with a link to the page in the app; see `app.mail` for the rules.

- An invite goes to the address invited.
- A version waiting for review goes to the org's active admins and
  owners, not to whoever submitted it.
- A version approved or rejected goes to whoever submitted it, unless they
  decided it themselves.

Recipients are worked out when the message is sent, from active members
only: a deactivated admin is told nothing.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.mail import templates
from app.models.approval import Approval, ApproverNotice
from app.models.assistant import Assistant, AssistantVersion
from app.models.enums import MemberRole
from app.models.membership import Membership
from app.models.user import User
from app.services import mail, org_settings


async def _active_member(
    session: AsyncSession, org_id: uuid.UUID, user_id: uuid.UUID | None
) -> User | None:
    if user_id is None:
        return None
    return await session.scalar(
        select(User)
        .join(Membership, Membership.user_id == User.id)
        .where(
            Membership.org_id == org_id,
            Membership.user_id == user_id,
            Membership.deactivated_at.is_(None),
            User.is_active.is_(True),
        )
    )


async def invite(
    *,
    org_id: uuid.UUID,
    to: str,
    org_name: str,
    role: str,
    inviter: str,
    link: str,
    expires: datetime,
) -> bool:
    """True when the invite was queued to be emailed."""
    sent = await mail.send(
        templates.invite(
            to=to, org_name=org_name, role=role, inviter=inviter, link=link, expires=expires
        ),
        org_id=org_id,
    )
    return sent is not None


async def version_submitted(
    session: AsyncSession, assistant: Assistant, version: AssistantVersion
) -> int:
    """Tell the reviewers, unless the owner switched it off (7b.8).
    Returns how many were told."""
    if not (await org_settings.switches(session, assistant.org_id)).review_emails:
        return 0
    submitter = await session.get(User, version.created_by) if version.created_by else None
    reviewers = (
        await session.scalars(
            select(User)
            .join(Membership, Membership.user_id == User.id)
            .where(
                Membership.org_id == assistant.org_id,
                Membership.role.in_((MemberRole.admin, MemberRole.owner)),
                Membership.deactivated_at.is_(None),
                User.is_active.is_(True),
                User.id != version.created_by,
            )
        )
    ).all()
    for reviewer in reviewers:
        await mail.send(
            templates.version_submitted(
                to=reviewer.email,
                assistant_name=assistant.name,
                number=version.version_number,
                submitter=(submitter.name or submitter.email) if submitter else "Someone",
                assistant_id=str(assistant.id),
            ),
            org_id=assistant.org_id,
        )
    return len(reviewers)


async def version_reviewed(
    session: AsyncSession,
    assistant: Assistant,
    version: AssistantVersion,
    *,
    approved: bool,
    reviewer_id: uuid.UUID,
) -> bool:
    """Tell whoever submitted it, unless they decided it themselves or the
    owner switched it off (7b.8)."""
    if version.created_by is None or version.created_by == reviewer_id:
        return False
    if not (await org_settings.switches(session, assistant.org_id)).decision_emails:
        return False
    submitter = await _active_member(session, assistant.org_id, version.created_by)
    if submitter is None:
        return False
    await mail.send(
        templates.version_reviewed(
            to=submitter.email,
            assistant_name=assistant.name,
            number=version.version_number,
            approved=approved,
            assistant_id=str(assistant.id),
        ),
        org_id=assistant.org_id,
    )
    return True


#: More requests within this long of an approver's last email wait for one
#: "N requests wait" email (Phase 9.2), so a burst isn't a burst of mail.
BATCH_WINDOW = timedelta(minutes=15)


async def _notice(session: AsyncSession, org_id: uuid.UUID, user_id: uuid.UUID) -> ApproverNotice:
    row = await session.scalar(
        select(ApproverNotice).where(
            ApproverNotice.org_id == org_id, ApproverNotice.user_id == user_id
        )
    )
    if row is None:
        row = ApproverNotice(org_id=org_id, user_id=user_id)
        session.add(row)
    return row


async def approval_waiting(
    session: AsyncSession, approval: Approval, *, reminder: bool = False
) -> int:
    """Tell whoever decides an approver request that it waits (Phase 9.1):
    the assistant's named approvers and their stand-ins, or the admins when
    nobody is named. Never the asker. `reminder`: the one reminder, a day
    later by default.

    Batched (Phase 9.2): someone emailed in the last `BATCH_WINDOW` isn't
    emailed again now; the sweep sends them one email for everything that
    waited meanwhile (`send_batches`). A reminder always goes. The link
    opens the request's page after sign-in: nothing is decided from an
    email. Returns how many were emailed now."""
    from app.services import approvals

    if not (await org_settings.switches(session, approval.org_id)).approval_emails:
        return 0
    person = await session.get(User, approval.requested_by) if approval.requested_by else None
    assistant = (
        await session.get(Assistant, approval.assistant_id) if approval.assistant_id else None
    )
    now = datetime.now(UTC)
    told = 0
    for user_id in sorted(await approvals.deciders(session, approval), key=str):
        user = await _active_member(session, approval.org_id, user_id)
        if user is None:
            continue
        notice = await _notice(session, approval.org_id, user_id)
        if (
            not reminder
            and notice.last_sent_at is not None
            and notice.last_sent_at > now - BATCH_WINDOW
        ):
            notice.pending_since = notice.pending_since or now
            continue
        await mail.send(
            templates.approval_waiting(
                to=user.email,
                asker=(person.name or person.email) if person else "Someone",
                assistant_name=assistant.name if assistant else "An assistant",
                what=approval.title or "a request",
                reference=approvals.reference(approval),
                expires=approval.expires_at,
                approval_id=str(approval.id),
                reminder=reminder,
            ),
            org_id=approval.org_id,
        )
        notice.last_sent_at = now
        told += 1
    await session.commit()
    return told


async def send_batches(session: AsyncSession, now: datetime | None = None) -> int:
    """One email to each approver who had requests wait untold, once their
    window has passed (Phase 9.2), counting what still waits for them."""
    from app.services import approvals

    now = now or datetime.now(UTC)
    due = (
        await session.scalars(
            select(ApproverNotice).where(
                ApproverNotice.pending_since.is_not(None),
                or_(
                    ApproverNotice.last_sent_at.is_(None),
                    ApproverNotice.last_sent_at <= now - BATCH_WINDOW,
                ),
            )
        )
    ).all()
    sent = 0
    for notice in due:
        notice.pending_since = None
        user = await _active_member(session, notice.org_id, notice.user_id)
        membership = await session.scalar(
            select(Membership).where(
                Membership.org_id == notice.org_id, Membership.user_id == notice.user_id
            )
        )
        if user is None or membership is None:
            continue
        if not (await org_settings.switches(session, notice.org_id)).approval_emails:
            continue
        waiting = len(await approvals.list_decidable(session, membership))
        if waiting == 0:
            continue
        await mail.send(
            templates.approvals_waiting(to=user.email, count=waiting), org_id=notice.org_id
        )
        notice.last_sent_at = now
        sent += 1
    await session.commit()
    return sent


async def approval_outcome(session: AsyncSession, approval: Approval, status: str) -> bool:
    """Tell the asker how their approver request ended (Phase 9.1). Not for
    their own confirmations: they were there."""
    from app.services import approvals

    asker = await _active_member(session, approval.org_id, approval.requested_by)
    if asker is None:
        return False
    assistant = (
        await session.get(Assistant, approval.assistant_id) if approval.assistant_id else None
    )
    await mail.send(
        templates.approval_outcome(
            to=asker.email,
            assistant_name=assistant.name if assistant else "An assistant",
            what=approval.title or "your request",
            reference=approvals.reference(approval),
            status=str(status),
            approval_id=str(approval.id),
        ),
        org_id=approval.org_id,
    )
    return True


#: The public name for the needs-review sweep (Phase 10.3).
active_member = _active_member


async def reported_fixed(user: User, assistant: Assistant, number: int) -> bool:
    """Tell someone that what they reported was fixed (Phase 10.3): the
    assistant and the version, and a link to Chat. Never what they asked
    or what it answered. Only to an active member (`active_member`)."""
    sent = await mail.send(
        templates.reported_fixed(
            to=user.email,
            assistant_name=assistant.name,
            number=number,
            assistant_id=str(assistant.id),
        ),
        org_id=assistant.org_id,
    )
    return sent is not None


__all__ = [
    "BATCH_WINDOW",
    "active_member",
    "approval_outcome",
    "approval_waiting",
    "invite",
    "reported_fixed",
    "send_batches",
    "version_reviewed",
    "version_submitted",
]
