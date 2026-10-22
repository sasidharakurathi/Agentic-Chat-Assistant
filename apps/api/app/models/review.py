"""The needs-review queue's decisions (Phase 10.3).

An answer needs review when the person who asked gave it a thumbs down,
said it didn't solve their problem, or the turn failed or declined. Those
signals already live elsewhere (`message_feedback`, `conversation_outcomes`,
`runs`), so an *open* item is derived from them and has no row here. A row
is written when a builder decides: they turned it into a test case, or
dismissed it with a reason.

- **Keyed by the answer** (`message_id`, unique): one decision per answer,
  however many signals it got.
- **Goes with the conversation.** The foreign key to the answer cascades,
  so erasing the conversation (8.10) erases the decision too, and nobody
  is told about a fix to something that no longer exists.
- **"Told when it is fixed."** `fixed_version_number` is set once the test
  case passes on the live version; `notified_at` when the person who asked
  was emailed about it (once). The email names the assistant and the
  version, never what was asked.
"""

from __future__ import annotations

import enum
import uuid
from datetime import datetime

from sqlalchemy import Enum as SAEnum
from sqlalchemy import ForeignKey, Integer, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, UUIDPrimaryKeyMixin
from app.db.types import TZDateTime

#: The longest reason for dismissing an item.
REASON_MAX = 500


class ReviewState(enum.StrEnum):
    #: Not decided yet. Never stored: an open item has no row.
    open = "open"
    turned_into_case = "turned_into_case"
    dismissed = "dismissed"


class ReviewItem(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "review_items"
    __table_args__ = (UniqueConstraint("message_id", name="uq_review_items_message_id"),)

    org_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    assistant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("assistants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    #: The answer that needed review.
    message_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("messages.id", ondelete="CASCADE"), nullable=False
    )
    state: Mapped[ReviewState] = mapped_column(
        SAEnum(ReviewState, name="review_state", native_enum=False, length=20), nullable=False
    )
    #: The test case it became; null once that case is deleted.
    eval_case_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("eval_cases.id", ondelete="SET NULL"), index=True
    )
    dismissed_reason: Mapped[str | None] = mapped_column(String(REASON_MAX))
    decided_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    decided_at: Mapped[datetime | None] = mapped_column(TZDateTime())
    #: The live version its test case passed on.
    fixed_version_number: Mapped[int | None] = mapped_column(Integer)
    #: When the person who asked was emailed that it was fixed.
    notified_at: Mapped[datetime | None] = mapped_column(TZDateTime())
    created_at: Mapped[datetime] = mapped_column(
        TZDateTime(), server_default=func.now(), nullable=False
    )


__all__ = ["REASON_MAX", "ReviewItem", "ReviewState"]
