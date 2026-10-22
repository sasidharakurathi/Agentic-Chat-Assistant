"""Hand-offs to a person (Phase 9.5)."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, Field

from app.agent.caps_handoff import NOTE_MAX
from app.models.handoff import HandoffChannel, HandoffStatus
from app.schemas.common import ApiModel, ORMModel


class HandoffCreate(BaseModel):
    #: In the person's own words: what they need. Optional.
    note: str = Field(default="", max_length=NOTE_MAX)


class HandoffOut(ORMModel):
    id: uuid.UUID
    conversation_id: uuid.UUID
    status: HandoffStatus
    channel: HandoffChannel
    #: What the ticket system answered, when an action opened a ticket.
    ticket_ref: str | None
    #: Built from the conversation: its title, the last questions, the last
    #: answer and the note. Shown in the app; never emailed.
    summary: str
    created_at: datetime


class HandoffList(ApiModel):
    """A conversation's hand-offs, and whether it can be handed off now."""

    #: The assistant hands off, and this isn't a preview.
    available: bool
    #: Who is told. Empty when not available.
    contact_email: str
    #: A hand-off from the chat opens a ticket rather than emailing.
    opens_ticket: bool
    items: list[HandoffOut]


__all__ = ["HandoffCreate", "HandoffList", "HandoffOut"]
