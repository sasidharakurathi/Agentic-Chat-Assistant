"""What a turn remembers, and what happens after it (task 5.2).

The pure parts (estimating size, choosing recent messages, writing the
replay block, summarizing) are in `agent/history.py` and
`agent/titles.py`. This module does their database work for the chat
service and the worker:

- `plan_turn`: resume the SDK session, or start fresh from the summary and
  recent messages, or (history off) neither;
- `after_turn`: queue a summary when the conversation has grown past the
  assistant's threshold;
- `summarize`: the worker job's body;
- `start_title` / `apply_title`: name a conversation from its first message.

Nothing here may fail a turn. A summary or title that can't be made is
logged and skipped: the conversation carries on exactly as before.
"""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app import queue
from app.agent.events import TitleEvent
from app.agent.history import (
    HistoryMessage,
    estimate_tokens,
    get_summarizer,
    replay_block,
    split_recent,
)
from app.agent.titles import DEFAULT_TITLE, Title, get_titler
from app.db.session import get_sessionmaker
from app.logging import get_logger
from app.models.conversation import Conversation, Message, MessageRole
from app.models.usage import UsageEvent, UsageKind
from app.schemas.assistant_config import AssistantConfig

log = get_logger(__name__)

#: How long `done` may wait for a title still being written. The title is
#: started with the turn, so normally it is long finished by then.
TITLE_WAIT_S = 5.0


@dataclass
class HistoryPlan:
    #: The SDK session to resume, or None to start a fresh one.
    resume_id: str | None
    #: The earlier conversation to put in a fresh session's system prompt.
    replay: str | None
    #: The summary version this turn's session reflects (stored on the
    #: conversation after the turn; see `Conversation.session_summary_version`).
    summary_version: int


async def messages_since_summary(
    session: AsyncSession, conv: Conversation, exclude: uuid.UUID | None = None
) -> list[Message]:
    """The messages the summary doesn't cover yet, oldest first."""
    stmt = select(Message).where(Message.conversation_id == conv.id)
    if conv.summary_through_at is not None:
        stmt = stmt.where(Message.created_at > conv.summary_through_at)
    if exclude is not None:
        stmt = stmt.where(Message.id != exclude)
    rows = await session.scalars(stmt.order_by(Message.created_at, Message.id))
    return list(rows.all())


def _as_history(rows: list[Message]) -> list[HistoryMessage]:
    return [
        HistoryMessage(
            role="user" if m.role == MessageRole.user else "assistant",
            content=m.content or "",
            blocks=list(m.blocks or []),
        )
        for m in rows
    ]


async def plan_turn(
    session: AsyncSession,
    conv: Conversation,
    config: AssistantConfig,
    *,
    current_message_id: uuid.UUID,
    resume_id: str | None,
) -> HistoryPlan:
    """Resume, replay, or neither. `resume_id` is the session the chat
    service would resume (the conversation row's)."""
    if not config.memory.persist_history:
        # Every message on its own: nothing resumed, nothing replayed.
        return HistoryPlan(None, None, conv.summary_version)
    if resume_id and conv.session_summary_version == conv.summary_version:
        return HistoryPlan(resume_id, None, conv.summary_version)
    # A newer summary than the session was started from, or nothing to
    # resume: start fresh from what we stored.
    earlier = _as_history(await messages_since_summary(session, conv, current_message_id))
    older, recent = split_recent(earlier, config.memory.summarize_after_tokens)
    return HistoryPlan(
        None, replay_block(conv.summary, recent, omitted=len(older)), conv.summary_version
    )


async def after_turn(session: AsyncSession, conv: Conversation, config: AssistantConfig) -> bool:
    """Queue a summary when the unsummarized part is over the threshold.
    True when one was queued."""
    if not config.memory.persist_history:
        return False
    try:
        rows = _as_history(await messages_since_summary(session, conv))
        threshold = config.memory.summarize_after_tokens
        if estimate_tokens(conv.summary, rows) <= threshold:
            return False
        # Only when something is old enough to fold: if the recent messages
        # alone are over the threshold, a summary would change nothing and
        # would still cost a model call and a fresh session.
        older, _recent = split_recent(rows, threshold)
        if not older:
            return False
        return await queue.enqueue_summary(conv.id, conv.summary_version)
    except Exception:
        log.exception("summary_check_failed", conversation_id=str(conv.id))
        return False


def _usage(
    conv: Conversation, model: str, tokens_in: int, tokens_out: int, cost: float
) -> UsageEvent:
    return UsageEvent(
        org_id=conv.org_id,
        assistant_id=conv.assistant_id,
        conversation_id=conv.id,
        kind=UsageKind.llm,
        model=model,
        tokens_in=tokens_in,
        tokens_out=tokens_out,
        cost_usd=cost,
    )


async def summarize(conversation_id: uuid.UUID, expected_version: int) -> bool:
    """Fold all but the recent messages into the summary. The worker job.

    `expected_version` makes it idempotent and race-free: a job for a
    version that has already moved on does nothing, and the write only lands
    if the version is still the one this job read."""
    from app.services.chat import config_for  # the chat service imports this module

    async with get_sessionmaker()() as session:
        conv = await session.get(Conversation, conversation_id)
        if conv is None or conv.summary_version != expected_version:
            return False
        config = await config_for(session, conv)
        rows = await messages_since_summary(session, conv)
        older, _recent = split_recent(_as_history(rows), config.memory.summarize_after_tokens)
        if not older:
            return False
        result = await get_summarizer().summarize(conv.summary, older)
        if not result.text.strip():
            return False
        written = await session.execute(
            update(Conversation)
            .where(
                Conversation.id == conv.id,
                Conversation.summary_version == expected_version,
            )
            .values(
                summary=result.text,
                summary_through_at=rows[len(older) - 1].created_at,
                summary_version=expected_version + 1,
                cost_usd=Conversation.cost_usd + result.cost_usd,
            )
            .execution_options(synchronize_session=False)
        )
        if written.rowcount != 1:  # type: ignore[attr-defined]
            await session.rollback()
            return False
        if result.model is not None:
            session.add(
                _usage(conv, result.model, result.tokens_in, result.tokens_out, result.cost_usd)
            )
        await session.commit()
    log.info(
        "conversation_summarized",
        conversation_id=str(conversation_id),
        version=expected_version + 1,
        folded=len(older),
    )
    return True


# ── titles ───────────────────────────────────────────────────


def start_title(
    conv: Conversation, config: AssistantConfig, text: str
) -> asyncio.Task[Title] | None:
    """Start naming the conversation, in parallel with its first turn."""
    first_turn = conv.last_message_at is None
    if not (config.memory.auto_title and first_turn and conv.title == DEFAULT_TITLE):
        return None
    return asyncio.create_task(get_titler().title(text))


async def apply_title(
    session: AsyncSession, conv: Conversation, task: asyncio.Task[Title]
) -> TitleEvent | None:
    """Save the title unless someone renamed the conversation meanwhile."""
    try:
        title = await asyncio.wait_for(asyncio.shield(task), timeout=TITLE_WAIT_S)
    except Exception:
        task.cancel()
        log.warning("title_failed", conversation_id=str(conv.id))
        return None
    if not title.text:
        return None
    written = await session.execute(
        update(Conversation)
        .where(Conversation.id == conv.id, Conversation.title == DEFAULT_TITLE)
        .values(title=title.text, cost_usd=Conversation.cost_usd + title.cost_usd)
        .execution_options(synchronize_session=False)
    )
    if written.rowcount != 1:  # type: ignore[attr-defined]
        # Renamed meanwhile: nothing changed. Committed, not rolled back: a
        # rollback expires everything loaded in the turn's session, and the
        # turn still reads its saved message and run afterwards.
        await session.commit()
        return None
    if title.model is not None:
        session.add(_usage(conv, title.model, title.tokens_in, title.tokens_out, title.cost_usd))
    await session.commit()
    return TitleEvent(title=title.text)


__all__ = [
    "TITLE_WAIT_S",
    "HistoryPlan",
    "after_turn",
    "apply_title",
    "messages_since_summary",
    "plan_turn",
    "start_title",
    "summarize",
]
