"""Erasing for good, and what must not be erased (Phase 8.10).

- **An erasure** records each time conversations were deleted for good: by
  the retention rule, or erasing one person. It lists the conversation ids
  and, for a person, who, so that `scripts/reapply_erasures.py` can delete
  them again after a backup is restored. No content: ids only.
- **A legal hold** keeps one person's conversations, or one conversation,
  out of retention and erasure until it is lifted: a dispute or an
  investigation must not lose its evidence. The owner sets and lifts them.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import JSON, ForeignKey, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, UUIDPrimaryKeyMixin
from app.db.types import TZDateTime


class Erasure(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "erasures"

    org_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    #: "retention" or "person".
    kind: Mapped[str] = mapped_column(String(20), nullable=False)
    #: For a person: whose. Not a foreign key: it must outlive the account.
    user_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    #: For retention: conversations last active before this were erased.
    cutoff: Mapped[datetime | None] = mapped_column(TZDateTime())
    conversation_ids: Mapped[list[Any]] = mapped_column(JSON, nullable=False, default=list)
    #: What else went with a person: memory notes, feedback comments.
    memories: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    comments: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    #: What went with the conversations, counted before they went (Phase
    #: 10.5): messages, run traces, tool calls, summaries, share links,
    #: review decisions. Counts only, like everything here.
    details: Mapped[dict[str, Any]] = mapped_column(
        JSON, nullable=False, default=dict, server_default="{}"
    )
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = mapped_column(
        TZDateTime(), server_default=func.now(), nullable=False
    )


class LegalHold(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "legal_holds"

    org_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    #: A person (all their conversations), or one conversation.
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=True, index=True
    )
    conversation_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"), nullable=True, index=True
    )
    reason: Mapped[str] = mapped_column(String(500), nullable=False)
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = mapped_column(
        TZDateTime(), server_default=func.now(), nullable=False
    )
    released_at: Mapped[datetime | None] = mapped_column(TZDateTime())
    released_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )


__all__ = ["Erasure", "LegalHold"]
