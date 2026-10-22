"""The needs-review queue (Phase 10.3): complaints and unanswered questions
become test cases, and the person who asked is told when it is fixed.

- **What needs review**: an answer in a live conversation that its asker
  gave a thumbs down, or whose conversation they said didn't solve it
  ("Did this solve it?" = no: its last answer). Only what the person chose
  to send: the notice promises builders see feedback you send (8.7), not
  every turn that went wrong. A turn that failed or declined to answer is
  said on an item that is already there, never a reason on its own.
  Previews are the builders' own testing and evals the platform's, so
  neither counts. Open items are worked out from those signals; only a
  decision is stored (`models.review`).
- **Who sees it**: the assistant's builders, with the question, the answer
  and the comment, never who asked: the same rule as the Feedback tab
  (8.8). An admin also gets the conversation, to open with a recorded read.
- **What a builder does**: turn it into a test case in one of the
  assistant's suites (the question, prefilled, and what a good answer
  looks like), or dismiss it with a reason.
- **"Told when it is fixed"**: a sweep (`fixed_sweep`, the worker's, every
  five minutes) looks for items whose test case passed in the newest
  finished run on the version live now. Then the asker gets one email: the
  assistant's name, the version, a link to Chat. No question, no answer.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.errors import BadRequest, Conflict, NotFound
from app.logging import get_logger
from app.models.assistant import Assistant, AssistantStatus, AssistantVersion
from app.models.conversation import (
    Conversation,
    ConversationMode,
    Message,
    MessageRole,
    Run,
    RunStatus,
)
from app.models.evals import EvalCase, EvalCaseResult, EvalRun, EvalRunStatus, EvalSuite
from app.models.feedback import ConversationOutcome, MessageFeedback
from app.models.review import ReviewItem, ReviewState
from app.schemas.evals import MAX_CASES, MAX_INPUT_CHARS, EvalExpected, EvalLabels
from app.services import audit, notify

log = get_logger(__name__)

#: How much of a question and its answer the list shows (as Feedback does).
EXCERPT = 1500
#: The newest signals of each kind looked at for one listing.
SCAN = 500
#: Items the sweep checks in one go.
SWEEP_BATCH = 500

THUMBS_DOWN, NOT_SOLVED, FAILED, DECLINED = "thumbs_down", "not_solved", "failed", "declined"


@dataclass
class Signal:
    """Why one answer needs review."""

    reasons: set[str] = field(default_factory=set)
    comment: str = ""
    at: datetime | None = None
    version_number: int | None = None

    def saw(self, reason: str, at: datetime | None, version: int | None) -> None:
        self.reasons.add(reason)
        if at is not None and (self.at is None or _aware(at) > _aware(self.at)):
            self.at = at
        if self.version_number is None:
            self.version_number = version


@dataclass
class Item:
    id: uuid.UUID
    state: ReviewState
    reasons: list[str]
    comment: str
    question: str
    answer: str
    version_number: int | None
    created_at: datetime
    eval_case_id: uuid.UUID | None = None
    suite_id: uuid.UUID | None = None
    dismissed_reason: str | None = None
    decided_at: datetime | None = None
    fixed_version_number: int | None = None
    asker_told: bool = False
    conversation_id: uuid.UUID | None = None


def _aware(at: datetime) -> datetime:
    """SQLite hands back naive datetimes; they are UTC."""
    return at if at.tzinfo else at.replace(tzinfo=UTC)


def _live() -> list[Any]:
    return [Conversation.mode == ConversationMode.live, Conversation.eval_run_id.is_(None)]


async def _signals(
    session: AsyncSession, assistant: Assistant, only: Sequence[uuid.UUID] | None = None
) -> dict[uuid.UUID, Signal]:
    """Every answer of this assistant that needs review, newest signals
    first, with why. `only`: just these answers."""
    out: dict[uuid.UUID, Signal] = {}
    mine = [MessageFeedback.message_id.in_(list(only))] if only is not None else []
    down = (
        select(
            MessageFeedback.message_id,
            MessageFeedback.comment,
            MessageFeedback.updated_at,
            MessageFeedback.version_number,
        )
        .join(Conversation, Conversation.id == MessageFeedback.conversation_id)
        .where(
            MessageFeedback.assistant_id == assistant.id,
            MessageFeedback.rating == "down",
            *_live(),
            *mine,
        )
        .order_by(MessageFeedback.updated_at.desc())
        .limit(SCAN)
    )
    for mid, comment, at, version in (await session.execute(down)).all():
        signal = out.setdefault(mid, Signal())
        signal.saw(THUMBS_DOWN, at, version)
        if comment and not signal.comment:
            signal.comment = comment

    # "Did this solve it?" = no is about the conversation: its last answer
    # is the one that didn't.
    unsolved = (
        await session.execute(
            select(
                ConversationOutcome.conversation_id,
                ConversationOutcome.updated_at,
                ConversationOutcome.version_number,
            )
            .join(Conversation, Conversation.id == ConversationOutcome.conversation_id)
            .where(
                Conversation.assistant_id == assistant.id,
                ConversationOutcome.solved.is_(False),
                *_live(),
            )
            .order_by(ConversationOutcome.updated_at.desc())
            .limit(SCAN)
        )
    ).all()
    if unsolved:
        when = {cid: (at, version) for cid, at, version in unsolved}
        last: dict[uuid.UUID, uuid.UUID] = {}
        answers = await session.execute(
            select(Message.conversation_id, Message.id)
            .where(Message.conversation_id.in_(list(when)), Message.role == MessageRole.assistant)
            .order_by(Message.created_at)
        )
        for cid, mid in answers.all():
            last[cid] = mid
        for cid, mid in last.items():
            if only is None or mid in only:
                out.setdefault(mid, Signal()).saw(NOT_SOLVED, *when[cid])

    if not out:
        return out
    # A failed or declined turn is context on what the person reported,
    # never an item by itself: they didn't send it (see the docstring).
    failed = (
        select(Run.message_id, Run.status)
        .join(Conversation, Conversation.id == Run.conversation_id)
        .where(
            Conversation.assistant_id == assistant.id,
            Run.status.in_([RunStatus.error, RunStatus.refused]),
            Run.message_id.in_(list(out)),
        )
    )
    for mid, status in (await session.execute(failed)).all():
        if mid in out:
            out[mid].reasons.add(DECLINED if status is RunStatus.refused else FAILED)
    return out


async def _question(session: AsyncSession, answer: Message) -> str:
    """The person's message the answer replied to."""
    return (
        await session.scalar(
            select(Message.content)
            .where(
                Message.conversation_id == answer.conversation_id,
                Message.role == MessageRole.user,
                Message.created_at <= answer.created_at,
            )
            .order_by(Message.created_at.desc())
            .limit(1)
        )
        or ""
    )


async def _item(
    session: AsyncSession,
    mid: uuid.UUID,
    signal: Signal,
    row: ReviewItem | None,
    *,
    admin: bool,
) -> Item | None:
    answer = await session.get(Message, mid)
    if answer is None:
        return None
    suite_id = None
    if row is not None and row.eval_case_id is not None:
        suite_id = await session.scalar(
            select(EvalCase.suite_id).where(EvalCase.id == row.eval_case_id)
        )
    return Item(
        id=mid,
        state=row.state if row is not None else ReviewState.open,
        reasons=sorted(signal.reasons),
        comment=signal.comment,
        question=(await _question(session, answer))[:EXCERPT],
        answer=answer.content[:EXCERPT],
        version_number=signal.version_number,
        created_at=signal.at or answer.created_at,
        eval_case_id=row.eval_case_id if row is not None else None,
        suite_id=suite_id,
        dismissed_reason=row.dismissed_reason if row is not None else None,
        decided_at=row.decided_at if row is not None else None,
        fixed_version_number=row.fixed_version_number if row is not None else None,
        asker_told=row is not None and row.notified_at is not None,
        conversation_id=answer.conversation_id if admin else None,
    )


async def listing(
    session: AsyncSession,
    assistant: Assistant,
    *,
    state: ReviewState,
    admin: bool,
    limit: int = 50,
) -> list[Item]:
    """Open items, newest signal first; or the decided ones, newest
    decision first. Never who asked."""
    out: list[Item] = []
    if state is ReviewState.open:
        decided = set(
            (
                await session.scalars(
                    select(ReviewItem.message_id).where(ReviewItem.assistant_id == assistant.id)
                )
            ).all()
        )
        signals = await _signals(session, assistant)
        newest = sorted(
            ((mid, s) for mid, s in signals.items() if mid not in decided),
            key=lambda pair: _aware(pair[1].at) if pair[1].at else datetime.min.replace(tzinfo=UTC),
            reverse=True,
        )
        for mid, signal in newest[:limit]:
            item = await _item(session, mid, signal, None, admin=admin)
            if item is not None:
                out.append(item)
        return out
    rows = list(
        (
            await session.scalars(
                select(ReviewItem)
                .where(ReviewItem.assistant_id == assistant.id, ReviewItem.state == state)
                .order_by(ReviewItem.decided_at.desc())
                .limit(limit)
            )
        ).all()
    )
    signals = await _signals(session, assistant, only=[r.message_id for r in rows])
    for row in rows:
        item = await _item(
            session, row.message_id, signals.get(row.message_id, Signal()), row, admin=admin
        )
        if item is not None:
            out.append(item)
    return out


async def one(session: AsyncSession, assistant: Assistant, mid: uuid.UUID, *, admin: bool) -> Item:
    """One item as the list shows it, after a decision."""
    row = await session.scalar(
        select(ReviewItem).where(
            ReviewItem.message_id == mid, ReviewItem.assistant_id == assistant.id
        )
    )
    signals = await _signals(session, assistant, only=[mid])
    item = await _item(session, mid, signals.get(mid, Signal()), row, admin=admin)
    if item is None:
        raise NotFound("This answer isn't in the review queue", code="review_item_not_found")
    return item


async def _target(
    session: AsyncSession, assistant: Assistant, mid: uuid.UUID
) -> tuple[Message, ReviewItem | None]:
    """The answer, if it is in this assistant's queue (open or decided)."""
    missing = NotFound("This answer isn't in the review queue", code="review_item_not_found")
    answer = await session.get(Message, mid)
    if answer is None or answer.role is not MessageRole.assistant:
        raise missing
    conv = await session.get(Conversation, answer.conversation_id)
    if conv is None or conv.assistant_id != assistant.id:
        raise missing
    row = await session.scalar(select(ReviewItem).where(ReviewItem.message_id == mid))
    if row is None and mid not in await _signals(session, assistant, only=[mid]):
        raise missing
    return answer, row


async def to_case(
    session: AsyncSession,
    assistant: Assistant,
    mid: uuid.UUID,
    *,
    suite_id: uuid.UUID,
    question: str | None,
    expected: EvalExpected,
    actor_id: uuid.UUID,
    ip: str | None,
) -> ReviewItem:
    """Turn an answer that needed review into a test case in one of this
    assistant's suites. The case remembers where it came from
    (`metadata.source_message_id`); the item, which case it became."""
    answer, row = await _target(session, assistant, mid)
    if row is not None and row.state is ReviewState.turned_into_case and row.eval_case_id:
        raise Conflict("This answer is already a test case", code="review_item_decided")
    suite = await session.get(EvalSuite, suite_id)
    if suite is None or suite.assistant_id != assistant.id:
        raise NotFound("No such eval suite for this assistant", code="suite_not_found")
    text = (question if question is not None else await _question(session, answer)).strip()
    if not text:
        raise BadRequest("Write the question the test case asks", code="question_required")
    count = int(
        await session.scalar(
            select(func.count()).select_from(EvalCase).where(EvalCase.suite_id == suite.id)
        )
        or 0
    )
    if count >= MAX_CASES:
        raise BadRequest(
            f"This suite already holds {MAX_CASES} cases. Choose another suite, or remove some.",
            code="too_many_cases",
        )
    top = await session.scalar(
        select(func.max(EvalCase.position)).where(EvalCase.suite_id == suite.id)
    )
    case = EvalCase(
        suite_id=suite.id,
        org_id=suite.org_id,
        position=(top if top is not None else -1) + 1,
        input=text[:MAX_INPUT_CHARS],
        expected=expected.model_dump(),
        labels=EvalLabels().model_dump(),
        meta={"source_message_id": str(mid), "from": "needs_review"},
    )
    session.add(case)
    await session.flush()
    if row is None:
        row = ReviewItem(org_id=assistant.org_id, assistant_id=assistant.id, message_id=mid)
        session.add(row)
    row.state = ReviewState.turned_into_case
    row.eval_case_id = case.id
    row.dismissed_reason = None
    row.decided_by = actor_id
    row.decided_at = datetime.now(UTC)
    row.fixed_version_number = None
    row.notified_at = None
    await session.flush()
    await audit.record(
        session,
        action="review_item.to_case",
        org_id=assistant.org_id,
        actor_user_id=actor_id,
        target_type="review_item",
        target_id=row.id,
        meta={
            "assistant_id": str(assistant.id),
            "suite_id": str(suite.id),
            "eval_case_id": str(case.id),
        },
        ip=ip,
    )
    await session.commit()
    await session.refresh(row)
    return row


async def dismiss(
    session: AsyncSession,
    assistant: Assistant,
    mid: uuid.UUID,
    *,
    reason: str,
    actor_id: uuid.UUID,
    ip: str | None,
) -> ReviewItem:
    """Nothing to fix, said why. An answer that is already a test case
    stays one: delete the case in Evals first."""
    _answer, row = await _target(session, assistant, mid)
    if row is not None and row.state is ReviewState.dismissed:
        raise Conflict("This answer was already dismissed", code="review_item_decided")
    if row is not None and row.state is ReviewState.turned_into_case and row.eval_case_id:
        raise Conflict(
            "This answer is a test case. Delete the case in Evals first, then dismiss it.",
            code="review_item_decided",
        )
    if row is None:
        row = ReviewItem(org_id=assistant.org_id, assistant_id=assistant.id, message_id=mid)
        session.add(row)
    row.state = ReviewState.dismissed
    row.eval_case_id = None
    row.dismissed_reason = reason.strip()
    row.decided_by = actor_id
    row.decided_at = datetime.now(UTC)
    await session.flush()
    await audit.record(
        session,
        action="review_item.dismiss",
        org_id=assistant.org_id,
        actor_user_id=actor_id,
        target_type="review_item",
        target_id=row.id,
        # The reason is the builder's words about an answer, which may quote
        # it: it stays on the item, not in the audit log.
        meta={"assistant_id": str(assistant.id), "reason_length": len(row.dismissed_reason)},
        ip=ip,
    )
    await session.commit()
    await session.refresh(row)
    return row


# ── told when it is fixed ────────────────────────────────────


async def _passed_on_live(
    session: AsyncSession, item: ReviewItem, assistant: Assistant
) -> int | None:
    """The live version's number when the newest finished run of the item's
    case on that version passed it; else None."""
    latest = (
        await session.execute(
            select(EvalCaseResult.passed, EvalRun.version_number)
            .join(EvalRun, EvalRun.id == EvalCaseResult.eval_run_id)
            .where(
                EvalCaseResult.eval_case_id == item.eval_case_id,
                EvalRun.status == EvalRunStatus.done,
                EvalRun.assistant_version_id == assistant.current_version_id,
            )
            .order_by(EvalRun.finished_at.desc(), EvalRun.created_at.desc())
            .limit(1)
        )
    ).first()
    if latest is None or not latest[0]:
        return None
    if latest[1] is not None:
        return int(latest[1])
    number = await session.scalar(
        select(AssistantVersion.version_number).where(
            AssistantVersion.id == assistant.current_version_id
        )
    )
    return int(number) if number is not None else None


async def notify_fixed(session: AsyncSession, *, now: datetime | None = None) -> int:
    """Mark the items whose test case now passes on the live version as
    fixed, and email each asker once. Returns how many were emailed.

    Marked before the email is sent, so a failure to send never sends it
    twice: the email is a courtesy, the mark is the record."""
    rows = (
        await session.execute(
            select(ReviewItem, Assistant)
            .join(Assistant, Assistant.id == ReviewItem.assistant_id)
            .where(
                ReviewItem.state == ReviewState.turned_into_case,
                ReviewItem.eval_case_id.is_not(None),
                ReviewItem.fixed_version_number.is_(None),
                Assistant.current_version_id.is_not(None),
                Assistant.status == AssistantStatus.published,
            )
            .limit(SWEEP_BATCH)
        )
    ).all()
    told = 0
    for item, assistant in rows:
        number = await _passed_on_live(session, item, assistant)
        if number is None:
            continue
        asker = await session.scalar(
            select(Conversation.created_by)
            .join(Message, Message.conversation_id == Conversation.id)
            .where(Message.id == item.message_id)
        )
        item.fixed_version_number = number
        user = await notify.active_member(session, assistant.org_id, asker)
        if user is not None:
            item.notified_at = now or datetime.now(UTC)
        await session.commit()
        if user is None:
            continue
        try:
            await notify.reported_fixed(user, assistant, number)
            told += 1
        except Exception as exc:
            log.warning("reported_fixed_notify_failed", error=type(exc).__name__)
    return told


async def fixed_sweep() -> int:
    """The worker's: every five minutes."""
    from app.db.session import get_sessionmaker

    async with get_sessionmaker()() as session:
        return await notify_fixed(session)


__all__ = [
    "DECLINED",
    "EXCERPT",
    "FAILED",
    "NOT_SOLVED",
    "THUMBS_DOWN",
    "Item",
    "Signal",
    "dismiss",
    "fixed_sweep",
    "listing",
    "notify_fixed",
    "one",
    "to_case",
]
