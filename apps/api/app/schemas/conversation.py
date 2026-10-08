from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, Field

from app.models.conversation import ConversationStatus, MessageRole, RunStatus
from app.schemas.common import ApiModel, ORMModel


class ConversationCreate(BaseModel):
    title: str | None = Field(default=None, max_length=200)
    external_user_ref: str | None = Field(default=None, max_length=200)


class ConversationRename(BaseModel):
    title: str = Field(min_length=1, max_length=200)


class MessageIn(BaseModel):
    text: str = Field(min_length=1, max_length=32_000)


class ConversationSummary(ORMModel):
    id: uuid.UUID
    assistant_id: uuid.UUID
    assistant_version_id: uuid.UUID | None
    title: str
    external_user_ref: str | None = None
    #: Who started it: the only person who can reply in it or change it
    #: (Phase 7a.4). Null when that person's account is gone.
    created_by: uuid.UUID | None = None
    status: ConversationStatus
    cost_usd: Decimal
    token_usage: dict[str, Any]
    created_at: datetime
    last_message_at: datetime | None
    #: An answer is being written in it right now (QOS-01): it carries on
    #: whether or not anyone is watching, and can be watched again.
    running: bool = False


class TurnState(ApiModel):
    """The turn running in a conversation, if any (QOS-01)."""

    #: Attach with `GET /conversations/{id}/turns/{turn_id}/events`. Null
    #: when nothing is running.
    turn_id: str | None = None


class MessageOut(ORMModel):
    id: uuid.UUID
    role: MessageRole
    content: str
    blocks: list[Any]
    model: str | None
    tokens_in: int
    tokens_out: int
    latency_ms: int | None
    created_at: datetime


class RunOut(ORMModel):
    """One agent turn, as recorded: what it cost, how long it took, how it
    ended, and the trace id that finds it in the logs / tracing backend."""

    id: uuid.UUID
    conversation_id: uuid.UUID
    message_id: uuid.UUID | None
    trace_id: str | None
    #: The published version that answered; null when the draft did.
    version_number: int | None
    model: str | None
    effort: str | None
    driver: str | None
    num_turns: int
    tokens_in: int
    tokens_out: int
    cost_usd: Decimal
    duration_ms: int | None
    status: RunStatus
    error: str | None
    #: Why the model stopped ("end_turn", "refusal", ...), when reported.
    stop_reason: str | None = None
    #: The model that answered instead of `model`, when the fallback did.
    fallback_model: str | None = None
    #: How the router sorted the message (task 5.10), when one is wired in.
    route: str | None = None
    created_at: datetime


class RunStep(ApiModel):
    """A tool call the turn made, in order."""

    id: str | None = None
    name: str
    input: Any = None
    status: str | None = None
    output: str | None = None


class TraceApproval(ApiModel):
    """The approval a step waited on, and who answered."""

    status: str
    risk: str
    requested_at: datetime
    decided_at: datetime | None = None
    #: How long the step waited for a person.
    wait_ms: int | None = None
    #: Who answered: their name, or their email when they have none.
    decided_by: str | None = None


class TraceGuardrail(ApiModel):
    check: str
    where: str
    detail: str


class TraceStep(ApiModel):
    """One thing the run did, in order (task 5.9): a tool call, or a
    guardrail acting."""

    id: str | None = None
    kind: Literal["tool", "guardrail"]
    name: str
    #: The delegation this ran inside, for a subagent's own calls.
    parent_id: str | None = None
    #: When it started, from the start of the turn; None for runs saved
    #: before this was recorded.
    started_ms: int | None = None
    duration_ms: int | None = None
    status: str | None = None
    #: How the call was allowed: "auto", "approved", "denied", ...
    permission: str | None = None
    input: Any = None
    #: The start of the output (the whole of it is in the message).
    output: str | None = None
    #: What a subagent wrote while it worked, for a delegation.
    subagent_text: str | None = None
    approval: TraceApproval | None = None
    guardrail: TraceGuardrail | None = None
    #: The canvas nodes it touched.
    nodes: list[str] = Field(default_factory=list)


class RunDetail(RunOut):
    steps: list[RunStep]
    assistant_id: uuid.UUID
    #: The run step by step: calls with their timing, approvals, subagents'
    #: notes and guardrail findings.
    timeline: list[TraceStep] = Field(default_factory=list)
    #: Every canvas node the run touched.
    nodes: list[str] = Field(default_factory=list)
    #: Which graph the nodes are from: the published version that answered,
    #: or the current draft (which may have changed since).
    graph: Literal["version", "draft"] = "draft"


class ConversationDetail(ConversationSummary):
    #: The most recent messages, oldest-first. Not the whole history.
    messages: list[MessageOut]
    #: Pass to `GET /conversations/{id}/messages?cursor=` for older messages;
    #: null when `messages` is everything.
    messages_next_cursor: str | None = None
    #: The turn running now, to attach to; null when none is.
    turn_id: str | None = None


__all__ = [
    "ConversationCreate",
    "ConversationDetail",
    "ConversationRename",
    "ConversationSummary",
    "MessageIn",
    "MessageOut",
    "RunDetail",
    "RunOut",
    "RunStep",
    "TraceApproval",
    "TraceGuardrail",
    "TraceStep",
    "TurnState",
]
