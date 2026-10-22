"""How Chat uses your data (Phase 8.7, plan D18): the organisation's notice,
each revision of it kept, and who accepted which version.

A revision is every saved text; a version is what people accept. The owner
fixing a typo saves a revision of the same version; a real change starts a
new version, and everyone accepts again before their next message. With no
revision saved, the built-in text is version 1."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import ForeignKey, Integer, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, UUIDPrimaryKeyMixin
from app.db.types import TZDateTime


class PrivacyNotice(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "privacy_notices"
    __table_args__ = (
        UniqueConstraint("org_id", "revision", name="uq_privacy_notices_org_revision"),
    )

    org_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    #: Every save, from 1.
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    #: What people accept: new only when the owner asks everyone again.
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = mapped_column(
        TZDateTime(), server_default=func.now(), nullable=False
    )


class NoticeAcceptance(UUIDPrimaryKeyMixin, Base):
    """Someone read and accepted a version of the notice."""

    __tablename__ = "notice_acceptances"
    __table_args__ = (
        UniqueConstraint(
            "org_id", "user_id", "version", name="uq_notice_acceptances_org_user_version"
        ),
    )

    org_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    accepted_at: Mapped[datetime] = mapped_column(
        TZDateTime(), server_default=func.now(), nullable=False
    )


__all__ = ["NoticeAcceptance", "PrivacyNotice"]
