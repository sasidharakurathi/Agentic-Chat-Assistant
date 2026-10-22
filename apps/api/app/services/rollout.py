"""What the upgrade to Phase 7b carried over, and the operator's tools for
what it left (Phase 7b.8).

`report` is read-only: every organisation with its people, assistants and
spend, and what needs a decision: personal orgs with something in them,
organisations without an owner who can sign in, assistants that closed at
the approval gate, and, in single-org mode, more than one organisation.

`archive`, `unarchive` and `freeze` act on one organisation, for the
operator (`scripts.upgrade_report`); each is in that org's audit log.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from typing import Literal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.errors import BadRequest, NotFound
from app.config import settings
from app.models.assistant import Assistant, AssistantStatus
from app.models.budget import Budget, BudgetPeriod
from app.models.conversation import Conversation
from app.models.enums import MemberRole
from app.models.membership import Membership
from app.models.organization import Organization
from app.models.usage import UsageEvent
from app.models.user import User
from app.services import audit, audit_chain

Level = Literal["ok", "note", "warn"]

#: The org-wide limit a freeze sets: nothing more is spent there.
FROZEN = Decimal("0")


@dataclass
class OrgLine:
    id: uuid.UUID
    name: str
    personal: bool
    archived: str | None
    members: int
    owners: list[str]
    live: int
    closed: int
    paused: int
    conversations: int
    spent_usd: float


@dataclass
class Report:
    orgs: list[OrgLine] = field(default_factory=list)
    notes: list[tuple[Level, str]] = field(default_factory=list)


async def _count(session: AsyncSession, stmt):  # type: ignore[no-untyped-def]
    return int(await session.scalar(stmt.execution_options(all_orgs=True)) or 0)


async def _line(session: AsyncSession, org: Organization) -> OrgLine:
    owners = (
        await session.scalars(
            select(User.email)
            .join(Membership, Membership.user_id == User.id)
            .where(
                Membership.org_id == org.id,
                Membership.role == MemberRole.owner,
                Membership.deactivated_at.is_(None),
                User.is_active.is_(True),
            )
            .order_by(User.email)
            .execution_options(all_orgs=True)
        )
    ).all()

    def assistants(status: AssistantStatus):  # type: ignore[no-untyped-def]
        return (
            select(func.count())
            .select_from(Assistant)
            .where(Assistant.org_id == org.id, Assistant.status == status)
        )

    return OrgLine(
        id=org.id,
        name=org.name,
        personal=org.is_personal,
        archived=org.archived_reason or ("archived" if org.archived_at else None),
        members=await _count(
            session,
            select(func.count())
            .select_from(Membership)
            .where(Membership.org_id == org.id, Membership.deactivated_at.is_(None)),
        ),
        owners=list(owners),
        live=await _count(session, assistants(AssistantStatus.published)),
        closed=await _count(session, assistants(AssistantStatus.draft)),
        paused=await _count(session, assistants(AssistantStatus.archived)),
        conversations=await _count(
            session,
            select(func.count()).select_from(Conversation).where(Conversation.org_id == org.id),
        ),
        spent_usd=float(
            await session.scalar(
                select(func.coalesce(func.sum(UsageEvent.cost_usd), 0))
                .where(UsageEvent.org_id == org.id)
                .execution_options(all_orgs=True)
            )
            or 0
        ),
    )


async def report(session: AsyncSession) -> Report:
    out = Report()
    for org in (
        await session.scalars(select(Organization).order_by(Organization.created_at))
    ).all():
        out.orgs.append(await _line(session, org))
    open_orgs = [o for o in out.orgs if o.archived is None]
    for o in open_orgs:
        if not o.owners:
            out.notes.append(
                ("warn", f"{o.name}: no owner who can sign in. Promote someone in the database.")
            )
        if o.personal and (o.live or o.closed or o.paused or o.conversations or o.spent_usd):
            out.notes.append(
                (
                    "warn",
                    f"{o.name}: a personal organisation with assistants, conversations or "
                    "spend, outside the company's budgets. Archive or freeze it, or leave it.",
                )
            )
        if o.closed:
            out.notes.append(
                (
                    "note",
                    f"{o.name}: {o.closed} assistant(s) never published, closed in Chat until "
                    "a version is approved.",
                )
            )
    if settings.org_mode == "single" and len(open_orgs) > 1:
        out.notes.append(
            (
                "warn",
                f"ORG_MODE=single and {len(open_orgs)} organisations are open: archive the "
                "ones that aren't the company's.",
            )
        )
    found = await audit_chain.verify(session)
    out.notes.append(
        (
            "ok",
            f"audit log: {found.sealed} entries sealed, {found.unsealed} waiting for the "
            "worker, chain intact",
        )
        if found.intact
        else ("warn", f"audit log: the chain breaks at entry {found.broken_at}")
    )
    return out


async def stranded(session: AsyncSession, org_id: uuid.UUID) -> list[str]:
    """Members of `org_id` with no other open organisation to sign in to."""
    out = []
    users = (
        await session.scalars(
            select(Membership.user_id)
            .where(Membership.org_id == org_id, Membership.deactivated_at.is_(None))
            .execution_options(all_orgs=True)
        )
    ).all()
    for user_id in users:
        elsewhere = await session.scalar(
            select(Membership.org_id)
            .join(Organization, Organization.id == Membership.org_id)
            .where(
                Membership.user_id == user_id,
                Membership.org_id != org_id,
                Membership.deactivated_at.is_(None),
                Organization.archived_at.is_(None),
            )
            .limit(1)
            .execution_options(all_orgs=True)
        )
        if elsewhere is None:
            user = await session.get(User, user_id)
            out.append(user.email if user else str(user_id))
    return out


async def _org(session: AsyncSession, org_id: uuid.UUID) -> Organization:
    org = await session.get(Organization, org_id)
    if org is None:
        raise NotFound("No organisation has that id")
    return org


async def archive(session: AsyncSession, org_id: uuid.UUID, *, reason: str) -> Organization:
    org = await _org(session, org_id)
    if org.archived_at is not None:
        raise BadRequest("That organisation is archived already", code="org_archived")
    if not reason.strip():
        raise BadRequest("Say why: the reason is kept with the organisation", code="reason_needed")
    org.archived_at = datetime.now(UTC)
    org.archived_reason = reason.strip()[:200]
    await audit.record(
        session,
        action="org.archive",
        org_id=org.id,
        target_type="org",
        target_id=org.id,
        meta={"reason": org.archived_reason, "by": "operator"},
    )
    await session.commit()
    return org


async def unarchive(session: AsyncSession, org_id: uuid.UUID) -> Organization:
    org = await _org(session, org_id)
    if org.archived_at is None:
        raise BadRequest("That organisation isn't archived", code="org_not_archived")
    org.archived_at = None
    org.archived_reason = None
    await audit.record(
        session,
        action="org.unarchive",
        org_id=org.id,
        target_type="org",
        target_id=org.id,
        meta={"by": "operator"},
    )
    await session.commit()
    return org


async def freeze(session: AsyncSession, org_id: uuid.UUID) -> None:
    """Stop all spend in an org and keep it readable: its day and month
    limits go to zero, so chats and paid AI help stop there. Its admins
    see the limits on the Usage page; raising them lifts the freeze."""
    org = await _org(session, org_id)
    rows = {
        b.period: b
        for b in (
            await session.scalars(
                select(Budget)
                .where(Budget.org_id == org.id, Budget.scope_key == "org")
                .execution_options(all_orgs=True)
            )
        ).all()
    }
    for period in (BudgetPeriod.day, BudgetPeriod.month):
        row = rows.get(period)
        if row is None:
            session.add(Budget(org_id=org.id, scope_key="org", period=period, limit_usd=FROZEN))
        else:
            row.limit_usd = FROZEN
            row.updated_by = None
    await audit.record(
        session,
        action="budget.updated",
        org_id=org.id,
        target_type="organization",
        target_id=org.id,
        meta={"frozen": True, "by": "operator"},
    )
    await session.commit()


__all__ = ["OrgLine", "Report", "archive", "freeze", "report", "stranded", "unarchive"]
