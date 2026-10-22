"""Telling the admins when Chat stops for everyone (Phase 8.9).

Prometheus is optional and pages nobody, and a two-person team must not
learn about an outage from angry employees. So when something stops every
answer, the org's admins and owner are told: an email at most once an hour
for each kind of problem, and a banner in the app until it is resolved.

- **Raised** by what stops everyone: the provider's spending limit, the
  model credentials or billing refused, the runtime missing or too old, the
  organisation's or an assistant's budget used up, and many turns turned
  away as busy within a few minutes. One person reaching their own cap
  stops only them, and raises nothing.
- **One open alert** per org, kind and subject (an assistant, for its
  budget), counted each time.
- **Resolved** by the next answer that works in the org (for a budget, on
  that assistant), or by an admin marking it handled (audited).

Raising never fails a turn: a problem recording the alert is logged and
the turn carries on.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_sessionmaker
from app.logging import get_logger
from app.mail import templates
from app.models.alert import AdminAlert
from app.models.enums import MemberRole
from app.models.membership import Membership
from app.models.user import User
from app.services import audit, mail

log = get_logger(__name__)

#: Each kind in words, for the banner and the email.
KINDS: dict[str, str] = {
    "provider_spend_cap": "The model provider's spending limit has been reached",
    "billing": "The model account can't be charged",
    "auth_failed": "The model credentials were refused",
    "agent_not_installed": "The assistant runtime isn't installed on the server",
    "model_unsupported": "The assistant runtime is too old for the chosen model",
    "budget_org": "The organisation's budget is used up",
    "budget_assistant": "An assistant's budget is used up",
    "busy": "Many conversations are being turned away: the server is at capacity",
}

#: Failed turns that raise an alert by their code.
FROM_FAILURES = frozenset(
    {"provider_spend_cap", "billing", "auth_failed", "agent_not_installed", "model_unsupported"}
)
#: Resolved by an answer that works anywhere in the org.
ORG_WIDE = (*FROM_FAILURES, "budget_org")

EMAIL_EVERY = timedelta(hours=1)
#: Busy: this many turned away within the window before anyone is told,
#: and quiet for the window again counts as over.
BUSY_WINDOW = timedelta(minutes=5)
BUSY_ALERT_AT = 5


async def raise_alert(org_id: uuid.UUID, kind: str, *, subject: str = "", detail: str = "") -> None:
    """Record that `kind` happened; email the admins if it is due. In its
    own session, and never raising."""
    try:
        async with get_sessionmaker()() as session:
            await _raise(session, org_id, kind, subject, detail)
    except Exception:
        log.exception("alert_failed", kind=kind)


async def _raise(
    session: AsyncSession, org_id: uuid.UUID, kind: str, subject: str, detail: str
) -> None:
    now = datetime.now(UTC)
    row = await session.scalar(
        select(AdminAlert).where(
            AdminAlert.org_id == org_id,
            AdminAlert.kind == kind,
            AdminAlert.subject == subject,
            AdminAlert.resolved_at.is_(None),
        )
    )
    if row is None:
        row = AdminAlert(
            org_id=org_id,
            kind=kind,
            subject=subject,
            detail=detail[:500],
            count=1,
            first_at=now,
            last_at=now,
        )
        session.add(row)
    else:
        last = row.last_at if row.last_at.tzinfo else row.last_at.replace(tzinfo=UTC)
        if kind == "busy" and now - last > BUSY_WINDOW:
            # A new run of busy turns, not the tail of the last one.
            row.count, row.first_at = 1, now
        else:
            row.count += 1
        row.last_at = now
        if detail:
            row.detail = detail[:500]
    emailed = row.emailed_at
    if emailed is not None and emailed.tzinfo is None:
        emailed = emailed.replace(tzinfo=UTC)
    due = (kind != "busy" or row.count >= BUSY_ALERT_AT) and (
        emailed is None or now - emailed >= EMAIL_EVERY
    )
    if due:
        row.emailed_at = now
    await session.commit()
    if due:
        await _email(session, org_id, kind, row.detail)


async def _email(session: AsyncSession, org_id: uuid.UUID, kind: str, detail: str) -> None:
    admins = (
        await session.scalars(
            select(User)
            .join(Membership, Membership.user_id == User.id)
            .where(
                Membership.org_id == org_id,
                Membership.role.in_((MemberRole.admin, MemberRole.owner)),
                Membership.deactivated_at.is_(None),
                User.is_active.is_(True),
            )
        )
    ).all()
    for admin in admins:
        await mail.send(
            templates.admin_alert(to=admin.email, what=KINDS.get(kind, kind), detail=detail),
            org_id=org_id,
        )


async def resolve_after_answer(org_id: uuid.UUID, assistant_id: uuid.UUID | None) -> None:
    """An answer worked: whatever stopped everyone is over. One UPDATE,
    never raising."""
    try:
        async with get_sessionmaker()() as session:
            await session.execute(
                update(AdminAlert)
                .where(
                    AdminAlert.org_id == org_id,
                    AdminAlert.resolved_at.is_(None),
                    or_(
                        AdminAlert.kind.in_(ORG_WIDE),
                        (AdminAlert.kind == "budget_assistant")
                        & (AdminAlert.subject == str(assistant_id)),
                    ),
                )
                .values(resolved_at=datetime.now(UTC))
                .execution_options(synchronize_session=False)
            )
            await session.commit()
    except Exception:
        log.exception("alert_resolve_failed")


def _open(org_id: uuid.UUID, now: datetime):  # type: ignore[no-untyped-def]
    """Open, and for busy, still happening."""
    return (
        (AdminAlert.org_id == org_id)
        & AdminAlert.resolved_at.is_(None)
        & ((AdminAlert.kind != "busy") | (AdminAlert.last_at >= now - BUSY_WINDOW * 6))
    )


async def listed(session: AsyncSession, org_id: uuid.UUID, *, open_only: bool) -> list[AdminAlert]:
    """The org's alerts, newest first: the open ones, or the last 100."""
    now = datetime.now(UTC)
    stmt = select(AdminAlert).where(AdminAlert.org_id == org_id)
    if open_only:
        stmt = stmt.where(_open(org_id, now))
    return list((await session.scalars(stmt.order_by(AdminAlert.last_at.desc()).limit(100))).all())


async def resolve(
    session: AsyncSession,
    *,
    org_id: uuid.UUID,
    kind: str,
    subject: str,
    actor_id: uuid.UUID,
    ip: str | None,
) -> int:
    """An admin marks a problem handled. Returns how many were open."""
    rows = (
        await session.scalars(
            select(AdminAlert).where(
                AdminAlert.org_id == org_id,
                AdminAlert.kind == kind,
                AdminAlert.subject == subject,
                AdminAlert.resolved_at.is_(None),
            )
        )
    ).all()
    now = datetime.now(UTC)
    for row in rows:
        row.resolved_at, row.resolved_by = now, actor_id
    await audit.record(
        session,
        action="alert.resolve",
        org_id=org_id,
        actor_user_id=actor_id,
        target_type="alert",
        target_id=kind,
        meta={"kind": kind, "subject": subject or None, "open": len(rows)},
        ip=ip,
    )
    await session.commit()
    return len(rows)


__all__ = [
    "BUSY_ALERT_AT",
    "BUSY_WINDOW",
    "EMAIL_EVERY",
    "FROM_FAILURES",
    "KINDS",
    "listed",
    "raise_alert",
    "resolve",
    "resolve_after_answer",
]
