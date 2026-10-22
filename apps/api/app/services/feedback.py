"""Feedback on answers, and whether a conversation solved it (Phase 8.8).

- **Who rates**: the person whose conversation it is, on its answers. A
  thumbs can change, or be taken back; a comment goes with it.
- **Who reads**: the assistant's builders see the counts and each comment
  with just that one question and answer, never who asked (plan D5: the
  notice says so). Admins see which conversation it came from too; opening
  it is the usual recorded read (7b.2).
- **What counts**: live conversations only. Previews are the builders'
  own testing, evals are the platform's.
- **"Did this solve it?"** is one outcome per conversation, with no name,
  kept when the conversation goes (10.2 counts it).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.errors import BadRequest, NotFound
from app.models.assistant import Assistant, AssistantVersion
from app.models.conversation import Conversation, ConversationMode, Message, MessageRole
from app.models.feedback import ConversationOutcome, MessageFeedback
from app.security.redact import mask_typed_secrets

#: How much of a question and its answer the builders' list shows.
EXCERPT = 1500


async def _version_number(session: AsyncSession, conv: Conversation) -> int | None:
    if conv.assistant_version_id is None:
        return None
    version = await session.get(AssistantVersion, conv.assistant_version_id)
    return version.version_number if version else None


async def rate(
    session: AsyncSession,
    conv: Conversation,
    *,
    user_id: uuid.UUID,
    message_id: uuid.UUID,
    rating: str | None,
    comment: str = "",
) -> MessageFeedback | None:
    """Set this person's thumbs (and comment) on one answer; `rating=None`
    takes it back."""
    message = await session.get(Message, message_id)
    if (
        message is None
        or message.conversation_id != conv.id
        or message.role is not MessageRole.assistant
    ):
        raise NotFound("No such answer in this conversation", code="message_not_found")
    row = await session.scalar(
        select(MessageFeedback).where(
            MessageFeedback.message_id == message_id, MessageFeedback.user_id == user_id
        )
    )
    if rating is None:
        if row is not None:
            await session.delete(row)
            await session.commit()
        return None
    if row is None:
        row = MessageFeedback(
            org_id=conv.org_id,
            assistant_id=conv.assistant_id,
            conversation_id=conv.id,
            message_id=message_id,
            user_id=user_id,
            version_number=await _version_number(session, conv),
            rating=rating,
        )
        session.add(row)
    row.rating = rating
    # A password typed into a comment is hidden too (Phase 9.9).
    row.comment = mask_typed_secrets(comment.strip())[0]
    row.updated_at = datetime.now(UTC)
    await session.commit()
    return row


async def solve(session: AsyncSession, conv: Conversation, *, solved: bool) -> ConversationOutcome:
    """ "Did this solve it?": one answer per conversation, changeable."""
    answered = await session.scalar(
        select(func.count())
        .select_from(Message)
        .where(Message.conversation_id == conv.id, Message.role == MessageRole.assistant)
    )
    if not answered:
        raise BadRequest("There's no answer to judge yet", code="nothing_answered")
    row = await session.scalar(
        select(ConversationOutcome).where(ConversationOutcome.conversation_id == conv.id)
    )
    if row is None:
        row = ConversationOutcome(
            org_id=conv.org_id,
            assistant_id=conv.assistant_id,
            conversation_id=conv.id,
            version_number=await _version_number(session, conv),
            solved=solved,
        )
        session.add(row)
    row.solved = solved
    row.updated_at = datetime.now(UTC)
    await session.commit()
    return row


async def of_conversation(
    session: AsyncSession, conv: Conversation
) -> tuple[dict[str, tuple[str, str]], bool | None]:
    """The thumbs its starter gave each answer (rating, comment), and
    whether they said it solved it."""
    rows = await session.scalars(
        select(MessageFeedback).where(
            MessageFeedback.conversation_id == conv.id,
            MessageFeedback.user_id == conv.created_by,
        )
    )
    solved = await session.scalar(
        select(ConversationOutcome.solved).where(ConversationOutcome.conversation_id == conv.id)
    )
    return {str(r.message_id): (r.rating, r.comment) for r in rows}, solved


# ── for the assistant's builders ─────────────────────────────


@dataclass
class VersionCount:
    version_number: int | None
    up: int = 0
    down: int = 0


@dataclass
class Comment:
    rating: str
    comment: str
    question: str
    answer: str
    version_number: int | None
    created_at: datetime
    conversation_id: uuid.UUID | None = None


@dataclass
class Summary:
    versions: list[VersionCount] = field(default_factory=list)
    solved_yes: int = 0
    solved_no: int = 0
    #: Live conversations with an answer: how many could have said.
    conversations: int = 0
    comments: list[Comment] = field(default_factory=list)


def _live(stmt):  # type: ignore[no-untyped-def]
    return stmt.join(Conversation, Conversation.id == MessageFeedback.conversation_id).where(
        Conversation.mode == ConversationMode.live, Conversation.eval_run_id.is_(None)
    )


async def summary(
    session: AsyncSession, assistant: Assistant, *, admin: bool, limit: int = 50
) -> Summary:
    """Thumbs by version, the solved rate with how many answered, and the
    latest comments with their question and answer. Never who asked; the
    conversation only for an admin."""
    out = Summary()
    counts = await session.execute(
        _live(
            select(MessageFeedback.version_number, MessageFeedback.rating, func.count())
            .where(MessageFeedback.assistant_id == assistant.id)
            .group_by(MessageFeedback.version_number, MessageFeedback.rating)
        )
    )
    by_version: dict[int | None, VersionCount] = {}
    for number, rating, n in counts.all():
        row = by_version.setdefault(number, VersionCount(number))
        if rating == "up":
            row.up += int(n)
        else:
            row.down += int(n)
    out.versions = sorted(by_version.values(), key=lambda v: -(v.version_number or 0))

    outcomes = await session.execute(
        select(ConversationOutcome.solved, func.count())
        .where(ConversationOutcome.assistant_id == assistant.id)
        .group_by(ConversationOutcome.solved)
    )
    for solved, n in outcomes.all():
        if solved:
            out.solved_yes = int(n)
        else:
            out.solved_no = int(n)
    out.conversations = int(
        await session.scalar(
            select(func.count())
            .select_from(Conversation)
            .where(
                Conversation.assistant_id == assistant.id,
                Conversation.mode == ConversationMode.live,
                Conversation.eval_run_id.is_(None),
                Conversation.last_message_at.is_not(None),
            )
        )
        or 0
    )

    rows = (
        await session.scalars(
            _live(
                select(MessageFeedback)
                .where(MessageFeedback.assistant_id == assistant.id, MessageFeedback.comment != "")
                .order_by(MessageFeedback.updated_at.desc())
                .limit(limit)
            )
        )
    ).all()
    for r in rows:
        answer = await session.get(Message, r.message_id)
        question = (
            await session.scalar(
                select(Message.content)
                .where(
                    Message.conversation_id == r.conversation_id,
                    Message.role == MessageRole.user,
                    Message.created_at <= answer.created_at,
                )
                .order_by(Message.created_at.desc())
                .limit(1)
            )
            if answer
            else None
        )
        out.comments.append(
            Comment(
                rating=r.rating,
                comment=r.comment,
                question=(question or "")[:EXCERPT],
                answer=(answer.content if answer else "")[:EXCERPT],
                version_number=r.version_number,
                created_at=r.updated_at,
                conversation_id=r.conversation_id if admin else None,
            )
        )
    return out


__all__ = [
    "EXCERPT",
    "Comment",
    "Summary",
    "VersionCount",
    "of_conversation",
    "rate",
    "solve",
    "summary",
]
