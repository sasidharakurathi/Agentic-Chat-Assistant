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
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Literal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.budget import Budget, BudgetPeriod
from app.models.usage import UsageEvent
from app.services import audit

#: Where the soft warning starts.
WARN_AT = 0.8

Scope = Literal["org", "assistant"]
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
        whose = "organisation's" if self.scope == "org" else "assistant's"
        return f"{whose} {'daily' if self.period is BudgetPeriod.day else 'monthly'} budget"

    def _name(self) -> str:
        return f"This {self.short}"

    def _resets(self) -> str:
        if self.period is BudgetPeriod.day:
            return "at midnight UTC"
        return f"on {self.resets_at.day} {self.resets_at:%B} (UTC)"

    def exceeded_message(self) -> str:
        return f"{self._name()} of {usd(self.limit_usd)} is used up. It resets {self._resets()}."

    def warning_message(self) -> str:
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


async def _spent(
    session: AsyncSession, org_id: uuid.UUID, assistant_id: uuid.UUID | None, since: datetime
) -> float:
    stmt = select(func.coalesce(func.sum(UsageEvent.cost_usd), 0)).where(
        UsageEvent.org_id == org_id, UsageEvent.created_at >= since
    )
    if assistant_id is not None:
        stmt = stmt.where(UsageEvent.assistant_id == assistant_id)
    return float(await session.scalar(stmt) or 0)


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
    now: datetime | None = None,
) -> Gate:
    """The org's budgets, and the assistant's if one is given."""
    now = now or datetime.now(UTC)
    keys = ["org"] if assistant_id is None else ["org", str(assistant_id)]
    rows = await session.scalars(
        select(Budget)
        .where(Budget.org_id == org_id, Budget.scope_key.in_(keys))
        .order_by(Budget.scope_key != "org", Budget.period)
    )
    return Gate([await _status(session, b, now) for b in rows.all()])


async def list_for_org(
    session: AsyncSession, org_id: uuid.UUID, *, now: datetime | None = None
) -> list[BudgetStatus]:
    """Every budget in the org, the org's own first."""
    now = now or datetime.now(UTC)
    rows = await session.scalars(
        select(Budget)
        .where(Budget.org_id == org_id)
        .order_by(Budget.scope_key != "org", Budget.scope_key, Budget.period)
    )
    return [await _status(session, b, now) for b in rows.all()]


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
    key = _scope_key(assistant_id)
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
            target_type="assistant" if assistant_id else "organization",
            target_id=assistant_id or org_id,
            meta=changed,
            ip=ip,
        )
    await session.commit()


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
    "WARN_AT",
    "BudgetStatus",
    "Gate",
    "gate",
    "list_for_org",
    "narrower",
    "period_bounds",
    "set_limits",
    "usd",
]
