"""How Chat uses your data (Phase 8.7): the notice, accepting it, admins'
reads of your conversations, and an assistant's card."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, Field

from app.schemas.common import ApiModel
from app.services.privacy import BODY_MAX


class ProcessorOut(ApiModel):
    """Someone outside the building who receives data, and what."""

    name: str
    what: str


class NoticeOut(ApiModel):
    """The notice in force, and whether you accepted it."""

    version: int
    body: str
    updated_at: datetime | None = None
    #: Who to ask about your data.
    contact: str
    #: Where messages go, from how this server is set up.
    processors: list[ProcessorOut]
    #: How long a quiet conversation is kept before it is erased (8.10).
    retention_days: int = 365
    #: When you accepted this version; null until you do.
    accepted_at: datetime | None = None
    #: Whether Chat waits for it before your first message.
    required: bool = True


class NoticeAccept(BaseModel):
    version: int = Field(ge=1)


class NoticeAdminOut(ApiModel):
    """The notice as admins see it in settings."""

    version: int
    revision: int
    body: str
    #: False while it is still the built-in text.
    custom: bool
    updated_at: datetime | None = None
    contact: str
    processors: list[ProcessorOut]
    #: How many people accepted the version in force.
    accepted: int
    default_body: str
    #: Whether the caller may change it: the owner.
    can_edit: bool


class NoticeUpdate(BaseModel):
    body: str = Field(min_length=1, max_length=BODY_MAX)
    #: A real change: everyone accepts it again before their next message.
    #: Off for a typo fixed.
    ask_again: bool = False


class NoticeRevisionOut(ApiModel):
    revision: int
    version: int
    body: str
    created_at: datetime
    created_by: str | None = None


class AccessReadOut(ApiModel):
    """An admin opened one of your conversations."""

    conversation_id: uuid.UUID | None = None
    conversation_title: str
    read_at: datetime
    reader: str
    reader_role: str
    reason: str


class UseOut(ApiModel):
    label: str
    #: An admin approves each time.
    approval: bool = False


class AssistantAboutOut(ApiModel):
    """An assistant's card: what its approved version uses and sends out."""

    name: str
    description: str
    owner: str | None = None
    version_number: int | None = None
    approved_at: datetime | None = None
    approved_by: str | None = None
    knowledge: list[str] = Field(default_factory=list)
    lookups: list[UseOut] = Field(default_factory=list)
    actions: list[UseOut] = Field(default_factory=list)
    memory_tool: bool = False
    keeps_history: bool = True
    leaves: list[ProcessorOut] = Field(default_factory=list)


__all__ = [
    "AccessReadOut",
    "AssistantAboutOut",
    "NoticeAccept",
    "NoticeAdminOut",
    "NoticeOut",
    "NoticeRevisionOut",
    "NoticeUpdate",
    "ProcessorOut",
    "UseOut",
]
