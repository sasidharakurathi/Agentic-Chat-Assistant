from __future__ import annotations

import enum
import uuid
from datetime import datetime

from sqlalchemy import Enum as SAEnum
from sqlalchemy import ForeignKey, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, UUIDPrimaryKeyMixin
from app.db.types import TZDateTime


class TokenPurpose(enum.StrEnum):
    password_reset = "password_reset"
    verify_email = "verify_email"
    #: Google sign-in's handoff (Phase 8.3): the callback gives the page this,
    #: and the page trades it for the session tokens, which never travel in
    #: an address. Lasts a minute.
    google_handoff = "google_handoff"
    #: Signing in on another device with a code (Phase 8.6): made while
    #: signed in, typed into the other device's sign-in page. Eight
    #: characters, five minutes. The way into an iPhone home-screen app,
    #: whose storage Safari (where Google sign-in ends) doesn't share.
    device_code = "device_code"


class UserToken(UUIDPrimaryKeyMixin, Base):
    """A one-time link sent by email (Phase 7b.6): resetting a password or
    confirming an address. Stored as a hash, like an invite; used once;
    short-lived. A newer one for the same purpose voids the older."""

    __tablename__ = "user_tokens"

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    purpose: Mapped[TokenPurpose] = mapped_column(
        SAEnum(TokenPurpose, name="token_purpose", native_enum=False, length=20), nullable=False
    )
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)
    expires_at: Mapped[datetime] = mapped_column(TZDateTime(), nullable=False)
    used_at: Mapped[datetime | None] = mapped_column(TZDateTime())
    created_at: Mapped[datetime] = mapped_column(
        TZDateTime(), server_default=func.now(), nullable=False
    )
