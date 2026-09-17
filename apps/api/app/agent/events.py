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


class ThinkingEvent(_Ev):
    type: Literal["thinking"] = "thinking"
    text: str


class ToolCallEvent(_Ev):
    type: Literal["tool_call"] = "tool_call"
    id: str
    name: str
    input: dict[str, Any] = Field(default_factory=dict)


class ToolResultEvent(_Ev):
    type: Literal["tool_result"] = "tool_result"
    id: str
    status: Literal["success", "error", "denied"]
    output: str = ""


class ApprovalRequiredEvent(_Ev):
    type: Literal["approval_required"] = "approval_required"
    approval_id: str
    tool: str
    input: dict[str, Any] = Field(default_factory=dict)
    risk: Literal["low", "medium", "high"] = "medium"


class UsageEvent(_Ev):
    type: Literal["usage"] = "usage"
    tokens_in: int = 0
    tokens_out: int = 0
    cost_usd: float = 0.0
    sdk_session_id: str | None = None
    num_turns: int | None = None
    terminal_reason: str | None = None


class ErrorEvent(_Ev):
    type: Literal["error"] = "error"
    code: str
    message: str


class DoneEvent(_Ev):
    type: Literal["done"] = "done"
    message_id: str
    run_id: str


AgentEvent = Annotated[
    TokenEvent
    | ThinkingEvent
    | ToolCallEvent
    | ToolResultEvent
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
    "DoneEvent",
    "ErrorEvent",
    "ThinkingEvent",
    "TokenEvent",
    "ToolCallEvent",
    "ToolResultEvent",
    "UsageEvent",
    "sse_frame",
]
