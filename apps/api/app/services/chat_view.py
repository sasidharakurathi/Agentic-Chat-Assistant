"""What an employee receives from Chat (Phase 8.1).

The Employee role chats; it doesn't build. Builders and admins see the
working parts of an answer: the SQL a tool ran, the rows it returned, the
model, the tokens and the cost. An employee sees the answer, its sources,
and that a tool was used, by a plain label. The server leaves the rest
out, in every response and every streamed event, so hiding it is not left
to the page (anyone can read a response in the browser's tools).

This is defence in depth, not the control: an employee can still ask the
model to repeat what a tool returned. What a tool may return to whom is
decided by the tool (plan D8, Phase 9.3).
"""

from __future__ import annotations

import json
from typing import Any

from app.models.enums import MemberRole
from app.models.membership import Membership
from app.schemas.approval import ApprovalOut
from app.schemas.conversation import ConversationSummary, MessageOut

#: The platform's own tools in words, by the name after `mcp__caps__`.
LABELS: dict[str, str] = {
    "sql_query": "Looked up data",
    "sql_introspect": "Looked up data",
    "sql_list_schemas": "Looked up data",
    "mongo_find": "Looked up data",
    "mongo_aggregate": "Looked up data",
    "kb_search": "Searched the knowledge base",
    "kb_list_sources": "Searched the knowledge base",
    "http_request": "Read a web page",
    "web_search": "Searched the web",
    "WebSearch": "Searched the web",
    "WebFetch": "Read a web page",
    "memory": "Checked its notes",
    "calculator": "Calculated",
    "datetime": "Checked the date",
}

#: Stream events an employee doesn't get at all: the model's working,
#: what a turn cost, and the org's and assistants' budget warnings (theirs
#: to act on is the limit itself). Their own allowance's warning is theirs
#: (Phase 8.9), without money.
DROPPED = frozenset({"thinking", "usage", "budget"})

_SERVER_PROBLEM = (
    "The assistant can't answer right now because of a problem on the server. "
    "Your admins can fix it; try again later."
)

#: Failures in an employee's words (Phase 8.6): what happened and what to
#: do, with no money, settings or panels in it. Any other code's message is
#: already written for anyone (`agent/errors.py`).
EMPLOYEE_ERRORS: dict[str, str] = {
    "busy": "Lots of people are asking right now. Try again in a few seconds.",
    "context_too_long": (
        "This conversation has got too long for the assistant. "
        "Start a new conversation to carry on."
    ),
    "auth_failed": _SERVER_PROBLEM,
    "billing": _SERVER_PROBLEM,
    "agent_not_installed": _SERVER_PROBLEM,
    "model_unsupported": _SERVER_PROBLEM,
    "internal_error": "Something went wrong while answering. Try again in a moment.",
    "provider_spend_cap": _SERVER_PROBLEM,
    # Their own allowance (Phase 8.9): the page adds when it is back.
    "person_limit": "You've reached your limit for now.",
}


def shaped(membership: Membership) -> bool:
    """Whether responses to this caller leave the working parts out."""
    return membership.role is MemberRole.employee


def tool_label(name: str) -> str:
    """A tool's name as an employee reads it."""
    short = name.removeprefix("mcp__caps__")
    if short in LABELS:
        return LABELS[short]
    if name.startswith("mcp__caps__act_"):
        # A declared action (Phase 9.1): its name, in words.
        words = short.removeprefix("act_").replace("_", " ")
        return words[:1].upper() + words[1:]
    if name.startswith("mcp__"):
        # mcp__<server>__<tool>: the server is what a person would recognise.
        parts = name.split("__")
        server = parts[1] if len(parts) > 2 else "a connected service"  # noqa: PLR2004
        return f"Used {server.replace('_', ' ')}"
    if name in ("Task", "Agent"):
        return "Asked a helper"
    return "Used a tool"


def request_title(name: str) -> str:
    """What a stored request for a free-form tool is called (Phase 9.1): a
    write, said as one. A declared action has its own title."""
    short = name.removeprefix("mcp__caps__")
    if short == "sql_query":
        return "A database change"
    if short == "http_request":
        return "A request to another system"
    if name.startswith("mcp__") and not name.startswith("mcp__caps__"):
        parts = name.split("__")
        if len(parts) > 2:  # noqa: PLR2004 - mcp__<server>__<tool>
            return f"{parts[2].replace('_', ' ')} on {parts[1].replace('_', ' ')}"
    return tool_label(name)


def _block(block: dict[str, Any]) -> dict[str, Any] | None:
    # Rows written before 2.9 have no "type": they are tool calls.
    kind = block.get("type", "tool_call")
    if kind == "tool_call":
        return {
            "type": "tool_call",
            "id": block.get("id"),
            "name": tool_label(str(block.get("name", ""))),
            "status": block.get("status"),
            "permission": block.get("permission"),
            "parent_id": block.get("parent_id"),
        }
    if kind == "thinking":
        return None
    return block


def message(m: MessageOut) -> MessageOut:
    blocks = [b for b in (_block(dict(x)) for x in m.blocks if isinstance(x, dict)) if b]
    content = m.content
    if any(b.get("type") == "request" for b in blocks):
        # How a request ended (Phase 9.1): its first line, in words. What it
        # did (a statement, rows, an error) stays with the builders.
        content = content.split("\n", 1)[0]
    return m.model_copy(
        update={
            "content": content,
            "blocks": blocks,
            "model": None,
            "tokens_in": 0,
            "tokens_out": 0,
            "latency_ms": None,
        }
    )


def summary(s: ConversationSummary) -> ConversationSummary:
    return s.model_copy(update={"cost_usd": 0, "token_usage": {}})


def approval(a: ApprovalOut) -> ApprovalOut:
    """What an employee sees of their own request: what kind of thing it is,
    where it stands, and who decides. A declared action's own values are
    theirs to check (Phase 9.1); the statement never reaches them, nor a
    free-form tool's input (D19)."""
    return a.model_copy(
        update={
            "tool_name": tool_label(a.tool_name),
            "input": a.input if a.action_name else {},
            "rationale": None,
            "result": _result_words(a.status.value) if a.result else None,
        }
    )


def _result_words(status: str) -> str:
    """How it ended, without the rows or the error text."""
    return {
        "succeeded": "Done.",
        "failed": "It didn't go through. Ask your IT team if it matters.",
    }.get(status, "")


def event(data: dict[str, Any]) -> dict[str, Any] | None:
    """One streamed event, shaped; None to leave it out."""
    kind = data.get("type")
    if kind in DROPPED:
        return _own_allowance(data) if kind == "budget" else None
    if kind == "tool_call":
        return {
            "type": "tool_call",
            "id": data.get("id"),
            "name": tool_label(str(data.get("name", ""))),
            "input": {},
            "parent_id": data.get("parent_id"),
        }
    if kind == "tool_result":
        return {**data, "output": "", "truncated": False}
    if kind == "error":
        return {**data, "message": error_words(data)}
    if kind == "approval_required":
        return {
            **data,
            "tool": tool_label(str(data.get("tool", ""))),
            # A declared action's values are the employee's own (Phase 9.1).
            "input": data.get("input", {}) if data.get("action_name") else {},
            "rationale": "",
            # Who decides: the employee confirms their own, an approver the rest.
            "decided_by": "asker" if data.get("tier") == "asker" else "approver",
        }
    return data


def _own_allowance(data: dict[str, Any]) -> dict[str, Any] | None:
    """A budget warning, for an employee: only their own allowance's
    (Phase 8.9), and without money."""
    if data.get("scope") != "person":
        return None
    when = "today" if data.get("period") == "day" else "this month"
    return {**data, "message": f"You've used most of your allowance for {when}."}


def error_words(data: dict[str, Any]) -> str:
    """A failed turn's message, for an employee."""
    code = str(data.get("code", ""))
    if code == "budget_exceeded":
        # An org or assistant budget says when it starts again, and the
        # page adds that in the reader's own time; a conversation's cap
        # doesn't.
        if data.get("resets_at"):
            return "This assistant has reached its usage limit for now."
        return "This conversation has reached its limit. Start a new conversation to carry on."
    return EMPLOYEE_ERRORS.get(code, str(data.get("message", "")))


def frame(raw: str) -> str | None:
    """A server-sent `data: {json}` frame, shaped; None to leave it out.
    Anything that isn't a JSON event passes as it is."""
    if not raw.startswith("data: "):
        return raw
    body = raw[len("data: ") :].strip()
    try:
        data = json.loads(body)
    except ValueError:
        return raw
    if not isinstance(data, dict):
        return raw
    shaped_event = event(data)
    if shaped_event is None:
        return None
    return f"data: {json.dumps(shaped_event, separators=(',', ':'))}\n\n"


__all__ = [
    "DROPPED",
    "EMPLOYEE_ERRORS",
    "LABELS",
    "approval",
    "error_words",
    "event",
    "frame",
    "message",
    "shaped",
    "summary",
    "tool_label",
]
