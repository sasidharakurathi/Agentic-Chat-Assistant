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
    #: Set on the final report (task 5.4): why the main loop stopped
    #: ("end_turn", "refusal", ...) and every model that answered, which
    #: shows when the fallback model stepped in.
    stop_reason: str | None = None
    models: list[str] = Field(default_factory=list)


class ErrorEvent(_Ev):
    """A turn failed, or ended without an answer (task 5.4: typed). `code` is
    stable (`agent/errors.py`); `message` is safe to show anyone; `retryable`
    says whether sending the message again may work."""

    type: Literal["error"] = "error"
    code: str
    message: str
    retryable: bool = False


class GuardrailEvent(_Ev):
    """A guardrail acted (task 5.3): what it checked, where, and what it did.
    Saved with the answer too, so the chat shows it after a reload."""

    type: Literal["guardrail"] = "guardrail"
    check: Literal["injection", "exfiltration", "pii", "schema", "budget"]
    where: Literal["user_message", "tool_input", "tool_result"]
    detail: str
    tool: str | None = None


class TitleEvent(_Ev):
    """The conversation's new title (task 5.2): sent once, on its first turn,
    just before `done`, when `memory.auto_title` named it."""

    type: Literal["title"] = "title"
    title: str


class BudgetEvent(_Ev):
    """A budget is 80% or more used (task 5.7): sent before the turn's own
    events, once per budget. Not saved: it describes the moment."""

    type: Literal["budget"] = "budget"
    scope: Literal["org", "assistant"]
    period: Literal["day", "month"]
    ratio: float
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
    | GuardrailEvent
    | TitleEvent
    | BudgetEvent
    | DoneEvent,
    Field(discriminator="type"),
]


def sse_frame(event: BaseModel) -> str:
    return f"data: {event.model_dump_json()}\n\n"


__all__ = [
    "AgentEvent",
    "ApprovalRequiredEvent",
    "BudgetEvent",
    "CitationEvent",
    "DoneEvent",
    "ErrorEvent",
    "GuardrailEvent",
    "ThinkingEvent",
    "TitleEvent",
    "TokenEvent",
    "ToolCallEvent",
    "ToolResultEvent",
    "UsageEvent",
    "sse_frame",
]
