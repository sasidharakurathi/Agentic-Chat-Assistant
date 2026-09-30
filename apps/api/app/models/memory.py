"""The memory tool's files (task 5.2, plan §4.7).

With `memory.memory_tool` on, the model gets a small file store under
`/memories` that outlives the conversation, in the shape of Anthropic's
memory tool: it can view, create, edit and delete files there.

**Scoped per assistant *and* per person** (`owner_key`): memory an assistant
builds about one user must never be read in another user's conversation
with the same assistant. See `agent/caps_memory.py` for how the key is
chosen.
"""

from __future__ import annotations

import uuid

from sqlalchemy import ForeignKey, Index, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class MemoryFile(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "memory_files"
    __table_args__ = (
        UniqueConstraint("assistant_id", "owner_key", "path", name="uq_memory_file_path"),
        Index("ix_memory_files_owner", "assistant_id", "owner_key"),
    )

    org_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    assistant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("assistants.id", ondelete="CASCADE"), nullable=False
    )
    #: Whose memory: "user:<id>", "ext:<external ref>" or "conv:<id>".
    owner_key: Mapped[str] = mapped_column(String(220), nullable=False)
    #: Always under /memories, normalised (see `caps_memory.normalize_path`).
    path: Mapped[str] = mapped_column(String(255), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False, default="")


__all__ = ["MemoryFile"]
