"""Analytics per assistant, with a baseline, and the managers' report
(Phase 10.1).

**What counts.** Live conversations only (`Conversation.mode == live`, no
`eval_run_id`): previews are the builders' own testing and evals are the
platform's, and neither says anything about whether the assistant helps
people. A conversation counts in the period it *started* in, once someone
asked something in it (`last_message_at` is set).

**The definitions**, kept simple and honest: we only call a conversation
resolved when the person said so, and never guess.

- **Resolved**: the person answered "Did this solve it?" yes (Phase 8.8,
  `ConversationOutcome.solved`).
- **Not resolved**: they answered no, or gave an answer a thumbs down and
  never said yes.
- **No verdict**: neither. Most conversations end this way; leaving
  without a word is counted as neither a success nor a failure.
- **Resolution rate** = resolved / (resolved + not resolved): of the people
  who said, always shown with how many said. None when nobody did.
- **With errors**: conversations in which a turn ended in an error. Counted
  apart: one can still be resolved.
- **People**: how many different people started one.
- **Spend**: what the usage ledger holds for these live conversations,
  counted on the day it was spent. **Cost per resolved conversation** =
  spend in the period / resolved in the period; none when nothing was
  resolved.
- **Baseline**: the same figures for the period of equal length just
  before, so a change can be shown next to each figure.

Periods are whole UTC days ending with today: `days=30` is today and the 29
days before it, and the baseline is the 30 days before that.

An outcome kept after its conversation was erased (8.10 clears its
`conversation_id`) still counts, as a conversation and as resolved or not,
on the day it was given; it has no person. The ledger keeps such a
conversation's spend but no longer knows it was live, so that spend is not
in these figures.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta
from typing import Any

from sqlalchemy import Select, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.assistant import Assistant
from app.models.conversation import Conversation, ConversationMode, Run, RunStatus
from app.models.feedback import ConversationOutcome, MessageFeedback
from app.models.usage import UsageEvent

#: The longest period one report covers.
MAX_DAYS = 365


@dataclass
class Figures:
    conversations: int = 0
    people: int = 0
    resolved: int = 0
    not_resolved: int = 0
    no_verdict: int = 0
    with_errors: int = 0
    spend_usd: float = 0.0

    @property
    def resolution_rate(self) -> float | None:
        said = self.resolved + self.not_resolved
        return round(self.resolved / said, 4) if said else None

    @property
    def cost_per_resolved_usd(self) -> float | None:
        return round(self.spend_usd / self.resolved, 6) if self.resolved else None


@dataclass
class Day:
    date: date
    conversations: int = 0
    resolved: int = 0
    spend_usd: float = 0.0


@dataclass
class Window:
    start: datetime
    end: datetime
    previous_start: datetime

    @property
    def days(self) -> int:
        return (self.end - self.start).days


@dataclass
class AssistantAnalytics:
    window: Window
    current: Figures
    previous: Figures
    series: list[Day]


@dataclass
class AssistantRow:
    assistant_id: uuid.UUID
    name: str
    status: str
    current: Figures
    previous: Figures


@dataclass
class OrgReport:
    window: Window
    rows: list[AssistantRow] = field(default_factory=list)
    total: Figures = field(default_factory=Figures)
    previous_total: Figures = field(default_factory=Figures)


def window(days: int, now: datetime | None = None) -> Window:
    """`days` whole UTC days ending with today, and the same length before."""
    today = (now or datetime.now(UTC)).astimezone(UTC).date()
    end = datetime.combine(today + timedelta(days=1), time.min, tzinfo=UTC)
    start = end - timedelta(days=days)
    return Window(start=start, end=end, previous_start=start - timedelta(days=days))


# ── reading ──────────────────────────────────────────────────


@dataclass
class _Conv:
    assistant_id: uuid.UUID
    at: datetime
    person: uuid.UUID | None
    verdict: str  # "resolved" | "not_resolved" | "no_verdict"
    errored: bool


def _live(stmt: Select[Any]) -> Select[Any]:
    return stmt.where(
        Conversation.mode == ConversationMode.live, Conversation.eval_run_id.is_(None)
    )


def _verdict(solved: bool | None, thumbs_down: bool) -> str:
    if solved is True:
        return "resolved"
    if solved is False or thumbs_down:
        return "not_resolved"
    return "no_verdict"


async def _conversations(
    session: AsyncSession,
    *,
    org_id: uuid.UUID,
    assistant_id: uuid.UUID | None,
    start: datetime,
    end: datetime,
) -> list[_Conv]:
    """Every live conversation started in [start, end), with its verdict,
    plus the outcomes whose conversation was erased since."""
    thumbs_down = (
        select(MessageFeedback.id)
        .where(MessageFeedback.conversation_id == Conversation.id, MessageFeedback.rating == "down")
        .exists()
    )
    errored = (
        select(Run.id)
        .where(Run.conversation_id == Conversation.id, Run.status == RunStatus.error)
        .exists()
    )
    stmt = _live(
        select(
            Conversation.assistant_id,
            Conversation.created_at,
            Conversation.created_by,
            ConversationOutcome.solved,
            thumbs_down.label("thumbs_down"),
            errored.label("errored"),
        )
        .outerjoin(ConversationOutcome, ConversationOutcome.conversation_id == Conversation.id)
        .where(
            Conversation.org_id == org_id,
            Conversation.last_message_at.is_not(None),
            Conversation.created_at >= start,
            Conversation.created_at < end,
        )
    )
    if assistant_id is not None:
        stmt = stmt.where(Conversation.assistant_id == assistant_id)
    out = [
        _Conv(
            assistant_id=aid,
            at=at,
            person=person,
            verdict=_verdict(solved, bool(down)),
            errored=bool(err),
        )
        for aid, at, person, solved, down, err in (await session.execute(stmt)).all()
    ]

    # Kept after an erasure: a version number means it was a live one (a
    # preview of the draft has none).
    erased = select(
        ConversationOutcome.assistant_id, ConversationOutcome.created_at, ConversationOutcome.solved
    ).where(
        ConversationOutcome.org_id == org_id,
        ConversationOutcome.conversation_id.is_(None),
        ConversationOutcome.assistant_id.is_not(None),
        ConversationOutcome.version_number.is_not(None),
        ConversationOutcome.created_at >= start,
        ConversationOutcome.created_at < end,
    )
    if assistant_id is not None:
        erased = erased.where(ConversationOutcome.assistant_id == assistant_id)
    out.extend(
        _Conv(assistant_id=aid, at=at, person=None, verdict=_verdict(solved, False), errored=False)
        for aid, at, solved in (await session.execute(erased)).all()
    )
    return out


def _spend_query(org_id: uuid.UUID, start: datetime, end: datetime) -> Select[Any]:
    return _live(
        select(Conversation.assistant_id, UsageEvent.created_at, UsageEvent.cost_usd)
        .join(Conversation, Conversation.id == UsageEvent.conversation_id)
        .where(
            UsageEvent.org_id == org_id,
            UsageEvent.created_at >= start,
            UsageEvent.created_at < end,
        )
    )


def _tally(convs: Iterable[_Conv], spend: float) -> Figures:
    f = Figures(spend_usd=round(spend, 6))
    people: set[uuid.UUID] = set()
    for c in convs:
        f.conversations += 1
        setattr(f, c.verdict, getattr(f, c.verdict) + 1)
        f.with_errors += int(c.errored)
        if c.person is not None:
            people.add(c.person)
    f.people = len(people)
    return f


# ── one assistant ────────────────────────────────────────────


async def for_assistant(
    session: AsyncSession, assistant: Assistant, *, days: int, now: datetime | None = None
) -> AssistantAnalytics:
    """This period's figures, the previous period's, and a daily series."""
    w = window(days, now)
    convs = await _conversations(
        session,
        org_id=assistant.org_id,
        assistant_id=assistant.id,
        start=w.previous_start,
        end=w.end,
    )
    spent = (
        await session.execute(
            _spend_query(assistant.org_id, w.previous_start, w.end).where(
                Conversation.assistant_id == assistant.id
            )
        )
    ).all()

    current = [c for c in convs if c.at >= w.start]
    previous = [c for c in convs if c.at < w.start]
    spend_now = sum(float(cost or 0) for _, at, cost in spent if at >= w.start)
    spend_before = sum(float(cost or 0) for _, at, cost in spent if at < w.start)

    series = {
        (w.start + timedelta(days=i)).date(): Day(date=(w.start + timedelta(days=i)).date())
        for i in range(w.days)
    }
    for c in current:
        day = series.get(c.at.astimezone(UTC).date())
        if day is not None:
            day.conversations += 1
            day.resolved += int(c.verdict == "resolved")
    for _, at, cost in spent:
        if at >= w.start:
            day = series.get(at.astimezone(UTC).date())
            if day is not None:
                day.spend_usd += float(cost or 0)
    for day in series.values():
        day.spend_usd = round(day.spend_usd, 6)

    return AssistantAnalytics(
        window=w,
        current=_tally(current, spend_now),
        previous=_tally(previous, spend_before),
        series=list(series.values()),
    )


# ── the managers' report ─────────────────────────────────────


async def for_org(
    session: AsyncSession, org_id: uuid.UUID, *, days: int, now: datetime | None = None
) -> OrgReport:
    """Every assistant of the organisation with its figures for this period
    and the previous one, busiest first, and the totals."""
    w = window(days, now)
    assistants = (
        await session.execute(
            select(Assistant.id, Assistant.name, Assistant.status).where(Assistant.org_id == org_id)
        )
    ).all()
    convs = await _conversations(
        session, org_id=org_id, assistant_id=None, start=w.previous_start, end=w.end
    )
    spent = (await session.execute(_spend_query(org_id, w.previous_start, w.end))).all()

    def spend_of(aid: uuid.UUID | None, current: bool) -> float:
        return sum(
            float(cost or 0)
            for a, at, cost in spent
            if (aid is None or a == aid) and (at >= w.start) == current
        )

    report = OrgReport(window=w)
    for aid, name, status in assistants:
        mine = [c for c in convs if c.assistant_id == aid]
        report.rows.append(
            AssistantRow(
                assistant_id=aid,
                name=name,
                status=getattr(status, "value", str(status)),
                current=_tally([c for c in mine if c.at >= w.start], spend_of(aid, True)),
                previous=_tally([c for c in mine if c.at < w.start], spend_of(aid, False)),
            )
        )
    report.rows.sort(key=lambda r: (-r.current.conversations, -r.current.spend_usd, r.name.lower()))
    report.total = _tally([c for c in convs if c.at >= w.start], spend_of(None, True))
    report.previous_total = _tally([c for c in convs if c.at < w.start], spend_of(None, False))
    return report


__all__ = [
    "MAX_DAYS",
    "AssistantAnalytics",
    "AssistantRow",
    "Day",
    "Figures",
    "OrgReport",
    "Window",
    "for_assistant",
    "for_org",
    "window",
]
