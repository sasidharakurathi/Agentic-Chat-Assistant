from __future__ import annotations

import enum
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import Enum as SAEnum
from sqlalchemy import ForeignKey, Index, Integer, String, UniqueConstraint, func, text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.db.types import JSONB, TZDateTime


class AssistantStatus(enum.StrEnum):
    """Where the assistant stands in Chat (Phase 7b.1). Set only by the
    lifecycle actions, never by an edit: `published` when an admin approves a
    version, `archived` and back when an admin pauses or reopens it."""

    #: No version approved yet: nobody can chat with it outside a preview.
    draft = "draft"
    #: An approved version answers in Chat.
    published = "published"
    #: Paused by an admin: nothing answers, previews included.
    archived = "archived"


class VersionStatus(enum.StrEnum):
    """A version's review state (Phase 7b.1)."""

    #: Waiting for an admin. At most one per assistant.
    submitted = "submitted"
    #: Approved by an admin. The live one is `Assistant.current_version_id`;
    #: earlier approved ones can be made live again (a rollback).
    approved = "approved"
    rejected = "rejected"
    #: A newer submission replaced it before anyone reviewed it.
    superseded = "superseded"
    #: Its builder took it back before review.
    withdrawn = "withdrawn"
    #: It was live and an admin rolled back from it. It cannot go live
    #: again, and its conversations move to the live version.
    rolled_back = "rolled_back"


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
    # The approved version Chat answers from, set only by approve and by an
    # admin's rollback (Phase 7b.1); never by an edit. Logical pointer to
    # assistant_versions.id (no FK: avoids a circular constraint).
    current_version_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    #: The builder's draft of the catalog listing and audience (Phase 8.5):
    #: frozen into the next submitted version, never shown from here.
    draft_listing: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default="{}"
    )
    #: Marked Official by an admin (Phase 8.5): shown first in the catalog.
    official_at: Mapped[datetime | None] = mapped_column(TZDateTime())
    official_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    #: Who decides this assistant's requests (Phase 9.1): `group_ids` and
    #: `user_ids`; empty means the admins. Set by an admin, outside versions:
    #: who approves can change without a new version (someone leaves).
    action_approvers: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default="{}"
    )


class AssistantVersion(UUIDPrimaryKeyMixin, Base):
    """An immutable snapshot submitted from the draft. No ``updated_at``: the
    graph and config never change; only the review fields do."""

    __tablename__ = "assistant_versions"
    __table_args__ = (
        UniqueConstraint("assistant_id", "version_number", name="uq_assistant_versions_number"),
        # One submission waiting per assistant: a new one supersedes it.
        Index(
            "uq_assistant_versions_one_submitted",
            "assistant_id",
            unique=True,
            postgresql_where=text("status = 'submitted'"),
            sqlite_where=text("status = 'submitted'"),
        ),
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
    # ── review (Phase 7b.1); `created_by`/`created_at` are the submission ──
    status: Mapped[VersionStatus] = mapped_column(
        SAEnum(VersionStatus, name="version_status", native_enum=False, length=20),
        nullable=False,
        default=VersionStatus.submitted,
    )
    reviewed_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    reviewed_at: Mapped[datetime | None] = mapped_column(TZDateTime())
    review_note: Mapped[str] = mapped_column(String(1000), nullable=False, default="")
    #: What the version uses beyond its config, fixed when it is submitted
    #: (Phase 7b.1, plan D16): its knowledge, document by document, and a
    #: fingerprint of each database connection and MCP server and tool. A
    #: live turn answers from these and drops anything that has drifted
    #: (`app.services.manifest`). Null for a version from before the gate
    #: until its first turn records one.
    manifest: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    manifest_hash: Mapped[str | None] = mapped_column(String(64))
    #: How Chat shows it and who it is for (Phase 8.5), as submitted:
    #: name, description, category, starters, audience. Null for a version
    #: from before, which is for everyone.
    listing: Mapped[dict[str, Any] | None] = mapped_column(JSONB)


class AssistantEditor(UUIDPrimaryKeyMixin, Base):
    """Someone besides its creator who may edit an assistant (Phase 7b.4).

    An assistant outlives the person who built it; co-editors keep it
    maintained. Granted and withdrawn by its creator or an admin, each
    audited. A co-editor edits, previews and submits like its creator, but
    does not grant editing to others."""

    __tablename__ = "assistant_editors"
    __table_args__ = (
        UniqueConstraint("assistant_id", "user_id", name="uq_assistant_editors_user"),
    )

    assistant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("assistants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    org_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    granted_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = mapped_column(
        TZDateTime(), server_default=func.now(), nullable=False
    )
