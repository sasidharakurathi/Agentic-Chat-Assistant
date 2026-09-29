"""The event stream a turn produces.

The runtime yields these; the SSE endpoint serialises each as one
``data: {json}\\n\\n`` frame. Kinds mirror ``packages/shared`` CHAT_EVENT_KINDS.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field


class _Ev(BaseModel):
    pass


class TokenEvent(_Ev):
    type: Literal["token"] = "token"
    text: str
    #: Set when this came from inside a subagent: the id of the delegation
    #: tool call the subagent is running under (task 2.10).
    parent_id: str | None = None


class ThinkingEvent(_Ev):
    type: Literal["thinking"] = "thinking"
    text: str
    #: Set when this came from inside a subagent: the id of the delegation
    #: tool call the subagent is running under (task 2.10).
    parent_id: str | None = None


class ToolCallEvent(_Ev):
    type: Literal["tool_call"] = "tool_call"
    id: str
    name: str
    input: dict[str, Any] = Field(default_factory=dict)
    #: Set when this came from inside a subagent: the id of the delegation
    #: tool call the subagent is running under (task 2.10).
    parent_id: str | None = None


class ToolResultEvent(_Ev):
    type: Literal["tool_result"] = "tool_result"
    id: str
    status: Literal["success", "error", "denied"]
    output: str = ""
    #: True when `output` is a preview: the model got the whole result, but
    #: what streams to the client and is stored on the message is capped.
    truncated: bool = False
    #: Set when this came from inside a subagent: the id of the delegation
    #: tool call the subagent is running under (task 2.10).
    parent_id: str | None = None
    #: How the call was permitted (task 4.7): "auto", "approved", "declined",
    #: "expired", "interrupted" or "refused". None for calls that never ask.
    permission: str | None = None


class CitationEvent(_Ev):
    """One ``[n]`` marker in the finished answer, resolved back to the chunk
    it came from. Emitted at finalize (after the token stream, after the
    message is persisted) — not mid-stream, because a marker only means
    anything once the registry has seen every search the turn made."""

    type: Literal["citation"] = "citation"
    marker: int
    chunk_id: str
    document_id: str
    data_source_id: str | None = None
    title: str = ""
    source_type: str | None = None
    uri: str | None = None
    snippet: str = ""
    score: float = 0.0
    loc: dict[str, Any] = Field(default_factory=dict)
    href: str | None = None
    spans: list[list[int]] = Field(default_factory=list)


class ApprovalRequiredEvent(_Ev):
    """A tool call is waiting on a human (task 3.8).

    ``rationale`` carries the *exact* thing that will run — for SQL, the
    statement itself. A reviewer cannot meaningfully answer "approve a
    database write?"; they can answer "approve `DELETE FROM sessions WHERE
    expired`?".
    """

    type: Literal["approval_required"] = "approval_required"
    approval_id: str
    tool: str
    input: dict[str, Any] = Field(default_factory=dict)
    risk: Literal["low", "medium", "high"] = "medium"
    rationale: str = ""
    expires_at: str | None = None


class UsageEvent(_Ev):
    type: Literal["usage"] = "usage"
    tokens_in: int = 0
    tokens_out: int = 0
    cost_usd: float = 0.0
    sdk_session_id: str | None = None
    num_turns: int | None = None
    terminal_reason: str | None = None
    #: Model calls this event accounts for (1 for the first usage report of a
    #: new API response). Lets the platform count turns as they happen.
    model_calls: int = 0
    #: Web searches the API ran this turn (usage.server_tool_use).
    web_searches: int = 0


class ErrorEvent(_Ev):
    type: Literal["error"] = "error"
    code: str
    message: str


class DoneEvent(_Ev):
    type: Literal["done"] = "done"
    message_id: str
    run_id: str
    trace_id: str | None = None


AgentEvent = Annotated[
    TokenEvent
    | ThinkingEvent
    | ToolCallEvent
    | ToolResultEvent
    | CitationEvent
    | ApprovalRequiredEvent
    | UsageEvent
    | ErrorEvent
    | DoneEvent,
    Field(discriminator="type"),
]


def sse_frame(event: BaseModel) -> str:
    return f"data: {event.model_dump_json()}\n\n"


__all__ = [
    "AgentEvent",
    "ApprovalRequiredEvent",
    "CitationEvent",
    "DoneEvent",
    "ErrorEvent",
    "ThinkingEvent",
    "TokenEvent",
    "ToolCallEvent",
    "ToolResultEvent",
    "UsageEvent",
    "sse_frame",
]
