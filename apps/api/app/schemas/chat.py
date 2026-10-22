"""What Chat shows (Phase 8.1): an assistant as a card, a conversation in a
list, and the admins' queue of requests waiting. Nothing of how an
assistant is built: that is the Studio's."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import Field

from app.models.assistant import AssistantStatus
from app.schemas.common import ApiModel


class ChatAssistantOut(ApiModel):
    id: uuid.UUID
    name: str
    description: str
    #: Whether it answers in Chat: an employee only ever sees `published`;
    #: a builder opening their own draft's card sees why it doesn't.
    status: AssistantStatus
    current_version_id: uuid.UUID | None = None
    #: The approved version Chat answers from.
    version_number: int | None = None
    #: Whether the caller may build it: the Studio link, for builders.
    can_edit: bool = False
    #: From the live version's listing (Phase 8.5).
    category: str = ""
    starters: list[str] = Field(default_factory=list)
    official: bool = False


class ChatConversationOut(ApiModel):
    id: uuid.UUID
    assistant_id: uuid.UUID
    assistant_name: str
    title: str
    created_at: datetime
    last_message_at: datetime | None = None


class ChatHelpOut(ApiModel):
    """What the Chat app keeps for when it can't reach the server (Phase
    8.6): the organisation's name and its own steps for getting on the
    office network or VPN."""

    organisation: str
    offline_help: str = ""


__all__ = ["ChatAssistantOut", "ChatConversationOut", "ChatHelpOut"]
