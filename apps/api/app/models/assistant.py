from __future__ import annotations

import enum
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import Enum as SAEnum
from sqlalchemy import ForeignKey, Integer, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.db.types import JSONB, TZDateTime


class AssistantStatus(enum.StrEnum):
    draft = "draft"
    published = "published"
    archived = "archived"


class Assistant(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "assistants"
    __table_args__ = (UniqueConstraint("org_id", "slug", name="uq_assistants_org_slug"),)

    org_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    slug: Mapped[str] = mapped_column(String(120), nullable=False, index=True)
    description: Mapped[str] = mapped_column(String(2000), nullable=False, default="")
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    status: Mapped[AssistantStatus] = mapped_column(
        SAEnum(AssistantStatus, name="assistant_status", native_enum=False, length=20),
        nullable=False,
        default=AssistantStatus.draft,
    )
    # Working copies. `draft_config` is always the compiled result of `draft_graph`
    # when the graph is valid (see app.services.assistants).
    draft_graph: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    draft_config: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    # Logical pointer to assistant_versions.id (no FK: avoids a circular constraint).
    current_version_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)


class AssistantVersion(UUIDPrimaryKeyMixin, Base):
    """An immutable snapshot published from the draft. No ``updated_at``."""

    __tablename__ = "assistant_versions"
    __table_args__ = (
        UniqueConstraint("assistant_id", "version_number", name="uq_assistant_versions_number"),
    )

    assistant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("assistants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    org_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    version_number: Mapped[int] = mapped_column(Integer, nullable=False)
    graph: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    config: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    note: Mapped[str] = mapped_column(String(1000), nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(
        TZDateTime(), server_default=func.now(), nullable=False
    )
