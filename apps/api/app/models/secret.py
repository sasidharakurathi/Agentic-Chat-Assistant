"""Envelope-encrypted secrets (plan §2.4).

Plaintext is never stored here and never logged. A row is inert without the
`APP_KEK` from the environment, so a database dump on its own reveals
nothing.
"""

from __future__ import annotations

import enum
import uuid
from datetime import datetime

from sqlalchemy import Enum as SAEnum
from sqlalchemy import ForeignKey, LargeBinary, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, UUIDPrimaryKeyMixin
from app.db.types import TZDateTime


class SecretKind(enum.StrEnum):
    """Bound into the AES-GCM additional data, so a credential sealed for one
    purpose cannot be replayed into another."""

    db_password = "db_password"
    #: A whole connection string (MongoDB). Sealed like a password because it
    #: usually *contains* one — in the userinfo, or in auth options.
    db_connection_uri = "db_connection_uri"
    mcp_headers = "mcp_headers"
    api_key = "api_key"


class Secret(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "secrets"

    org_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    kind: Mapped[SecretKind] = mapped_column(
        SAEnum(SecretKind, name="secret_kind", native_enum=False, length=32), nullable=False
    )
    ciphertext: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    dek_wrapped: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    nonce: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        TZDateTime(), server_default=func.now(), nullable=False
    )
    #: set when the wrapped DEK is re-sealed under a new KEK
    rotated_at: Mapped[datetime | None] = mapped_column(TZDateTime())


__all__ = ["Secret", "SecretKind"]
