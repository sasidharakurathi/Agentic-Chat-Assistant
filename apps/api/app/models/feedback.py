"""Feedback on answers, and whether a conversation solved it (Phase 8.8,
plan D11).

- **A thumbs** (up or down, with an optional comment) is one person's
  verdict on one answer, changeable. It goes with the conversation: when
  that is erased (8.10), so is the comment.
- **"Did this solve it?"** is one outcome per conversation, with no name
  on it. It stays when the conversation is removed or erased
  (`conversation_id` is set to null), so the analytics (10.2) still count
  it.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, ForeignKey, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, UUIDPrimaryKeyMixin
from app.db.types import TZDateTime

#: The longest comment.
COMMENT_MAX = 1000


class MessageFeedback(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "message_feedback"
    __table_args__ = (
        UniqueConstraint("message_id", "user_id", name="uq_message_feedback_message_user"),
    )

    org_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    assistant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("assistants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    message_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("messages.id", ondelete="CASCADE"), nullable=False, index=True
    )
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), index=True
    )
    #: "up" or "down".
    rating: Mapped[str] = mapped_column(String(8), nullable=False)
    comment: Mapped[str] = mapped_column(Text, nullable=False, default="")
    #: The approved version that answered; null for a draft preview.
    version_number: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(
        TZDateTime(), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        TZDateTime(), server_default=func.now(), nullable=False
    )


class ConversationOutcome(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "conversation_outcomes"
    __table_args__ = (
        UniqueConstraint("conversation_id", name="uq_conversation_outcomes_conversation"),
    )

    org_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    assistant_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("assistants.id", ondelete="SET NULL"), index=True
    )
    #: Null once the conversation is erased: the outcome still counts.
    conversation_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("conversations.id", ondelete="SET NULL")
    )
    version_number: Mapped[int | None] = mapped_column(Integer)
    solved: Mapped[bool] = mapped_column(Boolean, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        TZDateTime(), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        TZDateTime(), server_default=func.now(), nullable=False
    )


__all__ = ["COMMENT_MAX", "ConversationOutcome", "MessageFeedback"]
