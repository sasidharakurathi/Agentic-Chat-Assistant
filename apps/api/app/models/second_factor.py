"""A second factor for signing in (Phase 8.4): an authenticator app's
secret, sealed with the KEK like every stored credential, and ten
single-use recovery codes, stored hashed."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import BigInteger, ForeignKey, LargeBinary, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, UUIDPrimaryKeyMixin
from app.db.types import TZDateTime

#: The sealing kind: a value sealed as one thing can't be opened as another.
SEALED_KIND = "totp_secret"


class UserSecondFactor(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "user_second_factors"

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, unique=True, index=True
    )
    ciphertext: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    dek_wrapped: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    nonce: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    #: Set once the person typed a code from the app: until then it is a
    #: setup under way, and signs in nothing.
    enabled_at: Mapped[datetime | None] = mapped_column(TZDateTime())
    #: The last 30-second step a code was accepted for: the same code can't
    #: be used twice.
    last_step: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(
        TZDateTime(), server_default=func.now(), nullable=False
    )
    rotated_at: Mapped[datetime | None] = mapped_column(TZDateTime())


class UserRecoveryCode(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "user_recovery_codes"

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    code_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    used_at: Mapped[datetime | None] = mapped_column(TZDateTime())
    created_at: Mapped[datetime] = mapped_column(
        TZDateTime(), server_default=func.now(), nullable=False
    )


__all__ = ["SEALED_KIND", "UserRecoveryCode", "UserSecondFactor"]
