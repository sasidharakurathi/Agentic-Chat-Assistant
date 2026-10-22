"""An assistant's listing (Phase 8.5): how Chat's catalog shows it, and who
it is for. The builder edits a draft; submitting a version freezes it with
the version, and the admin reviews it with the rest (plan D16)."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator

from app.schemas.common import ApiModel

#: The longest a conversation starter may be.
STARTER_MAX = 200


class Audience(BaseModel):
    """Who sees it in Chat. Admins, the owner and its builders always do."""

    kind: Literal["everyone", "groups", "people"] = "everyone"
    group_ids: list[uuid.UUID] = Field(default_factory=list, max_length=50)
    user_ids: list[uuid.UUID] = Field(default_factory=list, max_length=200)


class Listing(BaseModel):
    category: str = Field(default="", max_length=40)
    #: A few questions that show someone what to ask.
    starters: list[str] = Field(default_factory=list, max_length=4)
    audience: Audience = Field(default_factory=Audience)

    @field_validator("category")
    @classmethod
    def _one_line(cls, v: str) -> str:
        return " ".join(v.split())

    @field_validator("starters")
    @classmethod
    def _starters(cls, v: list[str]) -> list[str]:
        cleaned = [" ".join(s.split()) for s in v]
        if any(len(s) > STARTER_MAX for s in cleaned):
            raise ValueError(f"a starter is at most {STARTER_MAX} characters")
        return [s for s in cleaned if s]


class VersionListing(Listing):
    """As frozen with a version: the name and description too."""

    name: str = ""
    description: str = ""


class GroupOut(ApiModel):
    id: uuid.UUID
    name: str
    description: str
    members: int = 0
    created_at: datetime


class GroupIn(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    description: str = Field(default="", max_length=500)


class GroupUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=80)
    description: str | None = Field(default=None, max_length=500)


class GroupMemberIn(BaseModel):
    user_id: uuid.UUID


class GroupMemberOut(ApiModel):
    user_id: uuid.UUID
    email: str
    name: str


class OfficialIn(BaseModel):
    official: bool


__all__ = [
    "Audience",
    "GroupIn",
    "GroupMemberIn",
    "GroupMemberOut",
    "GroupOut",
    "GroupUpdate",
    "Listing",
    "OfficialIn",
    "VersionListing",
]
