"""The needs-review queue over the API (Phase 10.3)."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from app.models.review import REASON_MAX
from app.schemas.common import ApiModel
from app.schemas.evals import MAX_INPUT_CHARS, EvalExpected

ReviewReason = Literal["thumbs_down", "not_solved", "failed", "declined"]


class ReviewItemOut(ApiModel):
    """An answer that needed review, with its question: never who asked."""

    #: The answer's id: what the actions name.
    id: uuid.UUID
    state: Literal["open", "turned_into_case", "dismissed"]
    #: Why it is here: a thumbs down, "didn't solve it"; and, noted on
    #: those, whether the turn failed or declined.
    reasons: list[ReviewReason]
    #: The asker's comment with their thumbs down, if any.
    comment: str
    question: str
    answer: str
    #: The approved version that answered.
    version_number: int | None = None
    #: When the newest signal came in.
    created_at: datetime
    #: The test case it became, and its suite (null once the case is deleted).
    eval_case_id: uuid.UUID | None = None
    suite_id: uuid.UUID | None = None
    dismissed_reason: str | None = None
    decided_at: datetime | None = None
    #: The live version its test case passed on.
    fixed_version_number: int | None = None
    #: Whether the person who asked was emailed that it was fixed.
    asker_told: bool = False
    #: For admins only: the conversation, to open with a recorded reason.
    conversation_id: uuid.UUID | None = None


class ReviewToCaseIn(BaseModel):
    suite_id: uuid.UUID
    #: The question the case asks; the asker's own question when left out.
    input: str | None = Field(default=None, max_length=MAX_INPUT_CHARS)
    #: What a good answer looks like.
    expected: EvalExpected = Field(default_factory=EvalExpected)


class ReviewDismissIn(BaseModel):
    reason: str = Field(min_length=3, max_length=REASON_MAX)


__all__ = ["ReviewDismissIn", "ReviewItemOut", "ReviewReason", "ReviewToCaseIn"]
