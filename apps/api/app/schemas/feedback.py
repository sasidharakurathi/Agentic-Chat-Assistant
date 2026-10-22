"""Feedback on answers, and whether a conversation solved it (Phase 8.8)."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from app.models.feedback import COMMENT_MAX
from app.schemas.common import ApiModel


class FeedbackIn(BaseModel):
    #: Null takes the thumbs (and its comment) back.
    rating: Literal["up", "down"] | None
    comment: str = Field(default="", max_length=COMMENT_MAX)


class FeedbackOut(ApiModel):
    rating: Literal["up", "down"] | None = None
    comment: str = ""


class OutcomeIn(BaseModel):
    solved: bool


class OutcomeOut(ApiModel):
    solved: bool


class VersionCountOut(ApiModel):
    version_number: int | None = None
    up: int
    down: int


class FeedbackCommentOut(ApiModel):
    """A comment, with just its question and answer: never who asked."""

    rating: Literal["up", "down"]
    comment: str
    question: str
    answer: str
    version_number: int | None = None
    created_at: datetime
    #: For admins only: the conversation, to open with a recorded reason.
    conversation_id: uuid.UUID | None = None


class FeedbackSummaryOut(ApiModel):
    versions: list[VersionCountOut]
    solved_yes: int
    solved_no: int
    #: Live conversations with an answer: how many could have said.
    conversations: int
    comments: list[FeedbackCommentOut]


__all__ = [
    "FeedbackCommentOut",
    "FeedbackIn",
    "FeedbackOut",
    "FeedbackSummaryOut",
    "OutcomeIn",
    "OutcomeOut",
    "VersionCountOut",
]
