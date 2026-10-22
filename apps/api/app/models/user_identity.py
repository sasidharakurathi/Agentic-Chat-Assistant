"""An outside sign-in linked to an account (Phase 8.3): Google, keyed on
Google's own id for the person (`sub`), which doesn't change when their
address does. Never matched by address: a renamed or recycled mailbox would
hand someone else the account."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import ForeignKey, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, UUIDPrimaryKeyMixin
from app.db.types import TZDateTime


class UserIdentity(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "user_identities"
    __table_args__ = (
        UniqueConstraint("provider", "subject", name="uq_user_identities_provider_subject"),
        # One Google account per person, so unlinking is unambiguous.
        UniqueConstraint("user_id", "provider", name="uq_user_identities_user_provider"),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    #: `google` (others, such as Microsoft, could follow).
    provider: Mapped[str] = mapped_column(String(20), nullable=False)
    #: The provider's own id for the person: Google's `sub`.
    subject: Mapped[str] = mapped_column(String(255), nullable=False)
    #: As they were when linked, for the person and the audit: the Google
    #: Workspace domain (`hd`) and the address.
    domain: Mapped[str | None] = mapped_column(String(255))
    email: Mapped[str] = mapped_column(String(320), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        TZDateTime(), server_default=func.now(), nullable=False
    )


__all__ = ["UserIdentity"]
