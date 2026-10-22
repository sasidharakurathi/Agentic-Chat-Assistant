from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import Enum as SAEnum
from sqlalchemy import ForeignKey, Index, String, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.db.types import TZDateTime
from app.models.enums import MemberRole

if TYPE_CHECKING:
    from app.models.organization import Organization


class Invite(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A pending invitation to join an org. The raw token is only ever shown once
    (returned by the create endpoint); we store a hash."""

    __tablename__ = "invites"
    __table_args__ = (
        # One open invite per address and org (Phase 7b.4): a new one voids
        # the old, so a forgotten link can't be used alongside a fresh one.
        Index(
            "uq_invites_one_open",
            "org_id",
            "email",
            unique=True,
            postgresql_where=text("accepted_at IS NULL AND revoked_at IS NULL"),
            sqlite_where=text("accepted_at IS NULL AND revoked_at IS NULL"),
        ),
    )

    org_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    email: Mapped[str] = mapped_column(String(320), nullable=False, index=True)
    role: Mapped[MemberRole] = mapped_column(
        SAEnum(MemberRole, name="member_role", native_enum=False, length=20),
        nullable=False,
        default=MemberRole.member,
    )
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)
    invited_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    expires_at: Mapped[datetime] = mapped_column(TZDateTime(), nullable=False)
    accepted_at: Mapped[datetime | None] = mapped_column(TZDateTime())
    #: Withdrawn by an admin, replaced by a newer invite, or voided when the
    #: person who sent it left (Phase 7b.4). A revoked link opens nothing.
    revoked_at: Mapped[datetime | None] = mapped_column(TZDateTime())
    revoked_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )

    organization: Mapped[Organization] = relationship()
