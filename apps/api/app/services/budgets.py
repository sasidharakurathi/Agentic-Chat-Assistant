"""Org and assistant budgets: what's spent, and whether to warn or stop
(task 5.7, PRD FR-42).

A budget is a limit for an org or one assistant, per UTC day or month.
What has been spent is summed from the `usage_events` ledger since the
period began, so it counts every paid call, whoever made it: chat turns,
the AI helpers, summaries and titles, ingestion.

- **At 80%** a turn starts with a warning, and the dashboard's bar turns
  amber.
- **At 100%** nothing new that spends is started: a turn is refused before
  it runs, as are the AI helpers on the real model and paid contextual
  retrieval. A turn already running stops at the tightest limit left
  (`Gate.remaining`), the same way it stops at the conversation's own cap.
  Free paths (the fake driver, templates) are never refused: they don't
  spend.

The per-conversation cap (`models.main.max_budget_usd`) stays where it was,
on the assistant's config.

**Per person** (Phase 8.9): an admin sets a cap for each Employee, and
another for each builder, admin or owner; and can raise or lower it for
one person. It counts everything with the person's name on it (their
conversations, previews and AI helpers), and reaching it stops only them.
Only admins see these, and everyone's spend.

**Builder testing** (Phase 9.7): previews and eval runs are the builders'
spend, not the pilot's. A turn in a preview conversation or an eval's
hidden one, with everything on the ledger under that conversation (its
title, summary, retrieval, the judge), counts toward the org's own
builder budget (`Switches.builder_monthly_usd`, a month, org-wide; none by
default) and never toward the org's or an assistant's budgets, so a busy
week of testing can't stop Chat. A live turn's org and assistant totals
leave that spend out. Per-person caps are unchanged: a builder's previews
still count toward, and are stopped by, their own cap, as before.

Spend is told apart through its conversation. A preview or eval
conversation that is deleted leaves its ledger rows without one, and they
then count as the pilot's: the safe way round, since it can only make the
pilot's budget stricter, never looser.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any, Literal

from sqlalchemy import ColumnElement, Select, exists, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.budget import Budget, BudgetPeriod
from app.models.conversation import Conversation, ConversationMode
from app.models.enums import MemberRole
from app.models.membership import Membership
from app.models.usage import UsageEvent
from app.services import audit, org_settings

#: Where the soft warning starts.
WARN_AT = 0.8

#: "builder": previews and eval runs, org-wide (Phase 9.7).
Scope = Literal["org", "assistant", "person", "builder"]

#: The per-person defaults (Phase 8.9), by the roles they cover.
ROLE_EMPLOYEE = "role:employee"
ROLE_BUILDER = "role:builder"
ROLE_KEYS = (ROLE_EMPLOYEE, ROLE_BUILDER)


def role_key(role: MemberRole) -> str:
    """The default a person's cap comes from: Employees', or everyone
    else's (builders, admins and the owner)."""
    return ROLE_EMPLOYEE if role is MemberRole.employee else ROLE_BUILDER


def person_key(user_id: uuid.UUID) -> str:
    """One person's own cap: 39 characters, inside the column's 40."""
    return f"person:{user_id.hex}"


def _shared(key: str) -> bool:
    """A budget anyone in the org may see: the org's or an assistant's."""
    return not key.startswith(("role:", "person:"))


State = Literal["ok", "warning", "exceeded"]


def usd(amount: float) -> str:
    """Money as people read it: cents, or up to four places below a dollar
    ("$0.0367 of $0.045"); rounding both to cents once read "$0.04 of
    $0.04"."""
    if amount >= 1:
        return f"${amount:.2f}"
    # Whichever says more: "0.04" vs "0.0367", but "0.50" vs "0.5".
    return "$" + max(f"{amount:.2f}", f"{amount:.4f}".rstrip("0"), key=len)


def period_bounds(period: BudgetPeriod, now: datetime) -> tuple[datetime, datetime]:
    """The start of the current UTC day or month, and when the next begins."""
    now = now.astimezone(UTC)
    if period is BudgetPeriod.day:
        start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        return start, start + timedelta(days=1)
    start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    # Any day 28 to 31 days on is next month; its first day is the reset.
    return start, (start + timedelta(days=32)).replace(day=1)


@dataclass
class BudgetStatus:
    scope: Scope
    assistant_id: uuid.UUID | None
    period: BudgetPeriod
    limit_usd: float
    spent_usd: float
    resets_at: datetime
    #: The person, for a per-person cap (Phase 8.9).
    user_id: uuid.UUID | None = None

    @property
    def ratio(self) -> float:
        return self.spent_usd / self.limit_usd if self.limit_usd > 0 else 1.0

    @property
    def remaining_usd(self) -> float:
        return max(0.0, self.limit_usd - self.spent_usd)

    @property
    def state(self) -> State:
        if self.spent_usd >= self.limit_usd:
            return "exceeded"
        return "warning" if self.ratio >= WARN_AT else "ok"

    @property
    def short(self) -> str:
        """e.g. "organisation's daily budget"."""
        when = "daily" if self.period is BudgetPeriod.day else "monthly"
        if self.scope == "person":
            return f"{when} limit"
        if self.scope == "builder":
            return f"{when} builder testing budget"
        whose = "organisation's" if self.scope == "org" else "assistant's"
        return f"{whose} {when} budget"

    def _name(self) -> str:
        return f"Your {self.short}" if self.scope == "person" else f"This {self.short}"

    def _resets(self) -> str:
        if self.period is BudgetPeriod.day:
            return "at midnight UTC"
        return f"on {self.resets_at.day} {self.resets_at:%B} (UTC)"

    def exceeded_message(self) -> str:
        if self.scope == "builder":
            # Who can do something about it, and where (Phase 9.7).
            return (
                f"Builder testing has used this month's {usd(self.limit_usd)}. "
                # The page's own breadcrumb, as the owner sees it.
                "The owner can raise it in Members › Organisation."  # noqa: RUF001
            )
        return f"{self._name()} of {usd(self.limit_usd)} is used up. It resets {self._resets()}."

    def warning_message(self) -> str:
        if self.scope == "builder":
            return (
                f"Builder testing is {int(self.ratio * 100)}% used this month "
                f"({usd(self.spent_usd)} of {usd(self.limit_usd)})."
            )
        return (
            f"{self._name()} is {int(self.ratio * 100)}% used "
            f"({usd(self.spent_usd)} of {usd(self.limit_usd)})."
        )


@dataclass
class Gate:
    """The budgets that apply to one assistant in one org, right now."""

    statuses: list[BudgetStatus] = field(default_factory=list)

    @property
    def exceeded(self) -> BudgetStatus | None:
        over = [s for s in self.statuses if s.state == "exceeded"]
        return over[0] if over else None

    @property
    def warnings(self) -> list[BudgetStatus]:
        return [s for s in self.statuses if s.state == "warning"]

    @property
    def tightest(self) -> BudgetStatus | None:
        return min(self.statuses, key=lambda s: s.remaining_usd, default=None)


def _builder_conversation() -> ColumnElement[bool]:
    """A preview or an eval case's hidden conversation (Phase 9.7)."""
    return or_(Conversation.mode == ConversationMode.preview, Conversation.eval_run_id.is_not(None))


def _live_only(stmt: Select[Any]) -> Select[Any]:
    """Leave out builder testing's spend. NOT EXISTS rather than an outer
    join: ledger rows with no conversation (helpers, indexing) stay in."""
    return stmt.where(
        ~exists().where(Conversation.id == UsageEvent.conversation_id, _builder_conversation())
    )


async def builder_spent(session: AsyncSession, org_id: uuid.UUID, since: datetime) -> float:
    """What previews and eval runs have spent in the org since `since`."""
    stmt = (
        select(func.coalesce(func.sum(UsageEvent.cost_usd), 0))
        .join(Conversation, Conversation.id == UsageEvent.conversation_id)
        .where(
            UsageEvent.org_id == org_id,
            UsageEvent.created_at >= since,
            _builder_conversation(),
        )
    )
    return float(await session.scalar(stmt) or 0)


async def builder_status(
    session: AsyncSession, org_id: uuid.UUID, now: datetime | None = None
) -> BudgetStatus | None:
    """The builder budget this month, or None when the owner set none."""
    limit = (await org_settings.switches(session, org_id)).builder_monthly_usd
    if limit is None:
        return None
    start, resets = period_bounds(BudgetPeriod.month, now or datetime.now(UTC))
    return BudgetStatus(
        scope="builder",
        assistant_id=None,
        period=BudgetPeriod.month,
        limit_usd=float(limit),
        spent_usd=await builder_spent(session, org_id, start),
        resets_at=resets,
    )


async def _spent(
    session: AsyncSession, org_id: uuid.UUID, assistant_id: uuid.UUID | None, since: datetime
) -> float:
    """The pilot's spend: everything but builder testing (Phase 9.7)."""
    stmt = _live_only(
        select(func.coalesce(func.sum(UsageEvent.cost_usd), 0)).where(
            UsageEvent.org_id == org_id, UsageEvent.created_at >= since
        )
    )
    if assistant_id is not None:
        stmt = stmt.where(UsageEvent.assistant_id == assistant_id)
    return float(await session.scalar(stmt) or 0)


async def _spent_by(
    session: AsyncSession, org_id: uuid.UUID, user_id: uuid.UUID, since: datetime
) -> float:
    """One person's spend in the org since `since` (Phase 8.9)."""
    return float(
        await session.scalar(
            select(func.coalesce(func.sum(UsageEvent.cost_usd), 0)).where(
                UsageEvent.org_id == org_id,
                UsageEvent.user_id == user_id,
                UsageEvent.created_at >= since,
            )
        )
        or 0
    )


async def person_limits(
    session: AsyncSession, org_id: uuid.UUID, user_id: uuid.UUID, role: MemberRole
) -> dict[BudgetPeriod, tuple[float, bool]]:
    """The caps that apply to one person, by period: (limit, whether it is
    their own override rather than their role's default)."""
    rows = (
        await session.scalars(
            select(Budget).where(
                Budget.org_id == org_id,
                Budget.scope_key.in_([role_key(role), person_key(user_id)]),
            )
        )
    ).all()
    out: dict[BudgetPeriod, tuple[float, bool]] = {}
    for b in sorted(rows, key=lambda r: r.scope_key.startswith("person:")):
        # The person's own row, sorted last, replaces the default.
        out[b.period] = (float(b.limit_usd), b.scope_key.startswith("person:"))
    return out


async def person_statuses(
    session: AsyncSession, membership: Membership, now: datetime
) -> list[BudgetStatus]:
    limits = await person_limits(session, membership.org_id, membership.user_id, membership.role)
    out = []
    for period, (limit, _own) in sorted(limits.items()):
        start, resets = period_bounds(period, now)
        out.append(
            BudgetStatus(
                scope="person",
                assistant_id=None,
                period=period,
                limit_usd=limit,
                spent_usd=await _spent_by(session, membership.org_id, membership.user_id, start),
                resets_at=resets,
                user_id=membership.user_id,
            )
        )
    return out


def _scope_key(assistant_id: uuid.UUID | None) -> str:
    return "org" if assistant_id is None else str(assistant_id)


async def _status(session: AsyncSession, b: Budget, now: datetime) -> BudgetStatus:
    start, resets = period_bounds(b.period, now)
    return BudgetStatus(
        scope="org" if b.assistant_id is None else "assistant",
        assistant_id=b.assistant_id,
        period=b.period,
        limit_usd=float(b.limit_usd),
        spent_usd=await _spent(session, b.org_id, b.assistant_id, start),
        resets_at=resets,
    )


async def gate(
    session: AsyncSession,
    org_id: uuid.UUID,
    assistant_id: uuid.UUID | None,
    *,
    person: Membership | None = None,
    now: datetime | None = None,
    builder: bool = False,
) -> Gate:
    """The org's budgets, the assistant's if one is given, and the person's
    (Phase 8.9) if they are.

    `builder`: a preview or an eval run (Phase 9.7). Then only the builder
    budget applies, with the person's own cap as before; the org's and the
    assistant's are the pilot's and don't."""
    now = now or datetime.now(UTC)
    statuses: list[BudgetStatus] = []
    if builder:
        own = await builder_status(session, org_id, now)
        if own is not None:
            statuses.append(own)
    else:
        keys = ["org"] if assistant_id is None else ["org", str(assistant_id)]
        rows = await session.scalars(
            select(Budget)
            .where(Budget.org_id == org_id, Budget.scope_key.in_(keys))
            .order_by(Budget.scope_key != "org", Budget.period)
        )
        statuses = [await _status(session, b, now) for b in rows.all()]
    if person is not None:
        statuses += await person_statuses(session, person, now)
    return Gate(statuses)


async def list_for_org(
    session: AsyncSession, org_id: uuid.UUID, *, now: datetime | None = None
) -> list[BudgetStatus]:
    """Every budget in the org, the org's own first. Not the per-person
    ones (Phase 8.9): this is shown to everyone in the org, and those are
    the admins'."""
    now = now or datetime.now(UTC)
    rows = await session.scalars(
        select(Budget)
        .where(Budget.org_id == org_id)
        .order_by(Budget.scope_key != "org", Budget.scope_key, Budget.period)
    )
    return [await _status(session, b, now) for b in rows.all() if _shared(b.scope_key)]


async def set_limits(
    session: AsyncSession,
    *,
    org_id: uuid.UUID,
    assistant_id: uuid.UUID | None,
    limits: dict[BudgetPeriod, float | None],
    actor_id: uuid.UUID,
    ip: str | None,
) -> None:
    """Set, change or (with None) remove the day and month limits of one
    scope. Every change is in the audit log (PRD FR-5)."""
    await _set(
        session,
        org_id=org_id,
        key=_scope_key(assistant_id),
        assistant_id=assistant_id,
        user_id=None,
        limits=limits,
        actor_id=actor_id,
        ip=ip,
        target=("assistant", assistant_id) if assistant_id else ("organization", org_id),
    )


async def set_person_default(
    session: AsyncSession,
    *,
    org_id: uuid.UUID,
    key: str,
    limits: dict[BudgetPeriod, float | None],
    actor_id: uuid.UUID,
    ip: str | None,
) -> None:
    """The cap each person in a role gets (Phase 8.9): `ROLE_EMPLOYEE` or
    `ROLE_BUILDER`."""
    assert key in ROLE_KEYS
    await _set(
        session,
        org_id=org_id,
        key=key,
        assistant_id=None,
        user_id=None,
        limits=limits,
        actor_id=actor_id,
        ip=ip,
        target=("budget_default", key),
    )


async def set_person_override(
    session: AsyncSession,
    *,
    org_id: uuid.UUID,
    user_id: uuid.UUID,
    limits: dict[BudgetPeriod, float | None],
    actor_id: uuid.UUID,
    ip: str | None,
) -> None:
    """One person's own cap, in place of their role's (Phase 8.9). Both
    limits None: back to the role's."""
    await _set(
        session,
        org_id=org_id,
        key=person_key(user_id),
        assistant_id=None,
        user_id=user_id,
        limits=limits,
        actor_id=actor_id,
        ip=ip,
        target=("user", user_id),
    )


async def _set(
    session: AsyncSession,
    *,
    org_id: uuid.UUID,
    key: str,
    assistant_id: uuid.UUID | None,
    user_id: uuid.UUID | None,
    limits: dict[BudgetPeriod, float | None],
    actor_id: uuid.UUID,
    ip: str | None,
    target: tuple[str, uuid.UUID | str],
) -> None:
    existing = {
        b.period: b
        for b in (
            await session.scalars(
                select(Budget).where(Budget.org_id == org_id, Budget.scope_key == key)
            )
        ).all()
    }
    changed: dict[str, dict[str, float | None]] = {}
    for period, limit in limits.items():
        row = existing.get(period)
        before = float(row.limit_usd) if row is not None else None
        if limit is None:
            if row is not None:
                await session.delete(row)
        elif row is None:
            session.add(
                Budget(
                    org_id=org_id,
                    assistant_id=assistant_id,
                    user_id=user_id,
                    scope_key=key,
                    period=period,
                    limit_usd=Decimal(str(limit)),
                    updated_by=actor_id,
                )
            )
        else:
            row.limit_usd = Decimal(str(limit))
            row.updated_by = actor_id
        if before != limit:
            changed[period.value] = {"from": before, "to": limit}
    if changed:
        await audit.record(
            session,
            action="budget.updated",
            org_id=org_id,
            actor_user_id=actor_id,
            target_type=target[0],
            target_id=target[1],
            # Per person (Phase 8.9): which default, or whose own cap.
            meta=changed if _shared(key) else {**changed, "scope": key},
            ip=ip,
        )
    await session.commit()


@dataclass
class PersonRow:
    """One member's spend and caps, for the admins (Phase 8.9)."""

    user_id: uuid.UUID
    name: str
    email: str
    role: MemberRole
    spent_day: float
    spent_month: float
    #: By period: (limit, whether it is their own override).
    limits: dict[BudgetPeriod, tuple[float, bool]]


async def people(
    session: AsyncSession, org_id: uuid.UUID, *, now: datetime | None = None
) -> tuple[dict[str, dict[BudgetPeriod, float]], list[PersonRow]]:
    """The per-person defaults, by role, and every active member's spend
    today and this month, with the caps that apply to them."""
    from app.models.user import User

    now = now or datetime.now(UTC)
    rows = (
        await session.scalars(
            select(Budget).where(
                Budget.org_id == org_id,
                Budget.scope_key.in_(ROLE_KEYS) | Budget.scope_key.startswith("person:"),
            )
        )
    ).all()
    defaults: dict[str, dict[BudgetPeriod, float]] = {k: {} for k in ROLE_KEYS}
    own: dict[uuid.UUID, dict[BudgetPeriod, float]] = {}
    for b in rows:
        if b.scope_key in ROLE_KEYS:
            defaults[b.scope_key][b.period] = float(b.limit_usd)
        elif b.user_id is not None:
            own.setdefault(b.user_id, {})[b.period] = float(b.limit_usd)

    async def spent(period: BudgetPeriod) -> dict[uuid.UUID, float]:
        start, _ = period_bounds(period, now)
        got = await session.execute(
            select(UsageEvent.user_id, func.coalesce(func.sum(UsageEvent.cost_usd), 0))
            .where(
                UsageEvent.org_id == org_id,
                UsageEvent.user_id.is_not(None),
                UsageEvent.created_at >= start,
            )
            .group_by(UsageEvent.user_id)
        )
        return {uid: float(total) for uid, total in got.all() if uid is not None}

    day, month = await spent(BudgetPeriod.day), await spent(BudgetPeriod.month)
    members = await session.execute(
        select(Membership, User)
        .join(User, User.id == Membership.user_id)
        .where(Membership.org_id == org_id, Membership.deactivated_at.is_(None))
        .order_by(User.email)
    )
    out = []
    for m, u in members.tuples().all():
        limits = {p: (v, False) for p, v in defaults[role_key(m.role)].items()}
        limits.update({p: (v, True) for p, v in own.get(u.id, {}).items()})
        out.append(
            PersonRow(
                user_id=u.id,
                name=u.name or "",
                email=u.email,
                role=m.role,
                spent_day=day.get(u.id, 0.0),
                spent_month=month.get(u.id, 0.0),
                limits=limits,
            )
        )
    return defaults, out


def narrower(conversation_remaining: float | None, g: Gate) -> tuple[float | None, str | None]:
    """The spend a turn may still make, and what to say when it's gone:
    the tighter of the conversation's own cap and the budgets."""
    tight = g.tightest
    if tight is None or (
        conversation_remaining is not None and conversation_remaining <= tight.remaining_usd
    ):
        return conversation_remaining, None
    return tight.remaining_usd, tight.exceeded_message()


__all__ = [
    "ROLE_BUILDER",
    "ROLE_EMPLOYEE",
    "ROLE_KEYS",
    "WARN_AT",
    "BudgetStatus",
    "Gate",
    "PersonRow",
    "builder_spent",
    "builder_status",
    "gate",
    "list_for_org",
    "narrower",
    "people",
    "period_bounds",
    "person_key",
    "role_key",
    "set_limits",
    "set_person_default",
    "set_person_override",
    "usd",
]
