"""Clearing up after a process that died mid-turn (Phase 7a.7).

A graceful stop records its running turns as stopped (`turns.shutdown`). A
hard one (an OOM kill, a crash, a container killed past its grace period)
records nothing: the approval it was waiting on stays "pending" forever, and
the question it was answering never gets a reply, so the person waits on a
conversation that will never move. The worker runs `sweep` every minute.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select

from app.db.session import get_sessionmaker
from app.logging import get_logger
from app.models.conversation import Conversation, ConversationStatus, Message, MessageRole
from app.services import approvals, turns

log = get_logger(__name__)

#: A question this old, with no turn alive in any process, was dropped.
#: Well past the wait for a turn slot (`AGENT_QUEUE_WAIT_S`) and the turn's
#: own liveness lapse (`turns.LIVE_TTL_S`).
UNANSWERED_AFTER_S = 120
#: Only questions this recent are looked at, to keep the scan small.
LOOK_BACK = timedelta(days=7)

INTERRUPTED_NOTICE = (
    "This message wasn't answered: the server stopped while it was being answered. Send it again."
)


async def notice_unanswered(now: datetime | None = None) -> int:
    """Reply to each question a dead turn left unanswered with a notice
    saying so. Nothing is done when it can't be told whether a turn is still
    running somewhere (no shared turn log, or Redis down): a notice on a
    conversation that is being answered would be wrong."""
    now = now or datetime.now(UTC)
    cutoff = now - timedelta(seconds=UNANSWERED_AFTER_S)
    async with get_sessionmaker()() as session:
        latest = (
            select(Message.conversation_id, func.max(Message.created_at).label("at"))
            .where(Message.created_at > now - LOOK_BACK)
            .group_by(Message.conversation_id)
            .subquery()
        )
        rows = (
            (
                await session.execute(
                    select(Message)
                    .join(
                        latest,
                        (Message.conversation_id == latest.c.conversation_id)
                        & (Message.created_at == latest.c.at),
                    )
                    .join(Conversation, Conversation.id == Message.conversation_id)
                    .where(
                        Message.role == MessageRole.user,
                        Message.created_at < cutoff,
                        Conversation.status != ConversationStatus.archived,
                        # An eval case's turn is read from its result, not answered.
                        Conversation.eval_run_id.is_(None),
                    )
                )
            )
            .scalars()
            .all()
        )
        if not rows:
            return 0
        alive = await turns.live_anywhere([m.conversation_id for m in rows])
        if alive is None:
            return 0
        dropped = [m for m in rows if m.conversation_id not in alive]
        for question in dropped:
            session.add(
                Message(
                    conversation_id=question.conversation_id,
                    org_id=question.org_id,
                    role=MessageRole.system,
                    content=INTERRUPTED_NOTICE,
                    parent_id=question.id,
                )
            )
        await session.commit()
    if dropped:
        log.info(
            "unanswered_noticed",
            count=len(dropped),
            conversations=[str(m.conversation_id) for m in dropped][:20],
        )
    return len(dropped)


async def sweep(now: datetime | None = None) -> tuple[int, int]:
    """(approvals closed, questions noticed)."""
    return await approvals.sweep_orphaned(now), await notice_unanswered(now)


__all__ = ["INTERRUPTED_NOTICE", "UNANSWERED_AFTER_S", "notice_unanswered", "sweep"]
