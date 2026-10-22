"""Traffic on the canvas (Phase 10.2, the signature feature): what the
assistant's live conversations did at each station, in the last hours.

For one assistant, per node of its **draft** graph (the drawing on the
builder's canvas):

- **calls**: tool calls that reached the node (`tool_calls` rows);
- **errors**: those calls that failed (`status == "error"`; a call a person
  declined is not an error);
- **approvals**: requests a call there raised for a person (`approvals`).

The agent node carries the turns (`runs`) and the turns that ended in an
error. Live conversations only, as in the analytics (10.1): previews and
evals are not traffic.

A call is placed with the run trace's own rules (`graph/trace_nodes.py`),
plus declared actions (`act_<name>`, Phase 9.1 / 9.3): a SQL action goes to
the database node that declares it, an HTTP action to the HTTP request tool
node. A call the draft has no node for any more (a tool since removed) is
counted in the totals as unplaced, never put on a guess.

Only counts leave this module: no inputs, outputs or names.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from pydantic import ValidationError
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.graph import trace_nodes
from app.graph.nodes import Graph
from app.models.approval import Approval
from app.models.assistant import Assistant
from app.models.conversation import Conversation, ConversationMode, Run, RunStatus, ToolCall
from app.models.integration import McpServer

#: The longest window one request covers: a month.
MAX_HOURS = 24 * 31


@dataclass
class NodeCount:
    calls: int = 0
    errors: int = 0
    approvals: int = 0


@dataclass
class Totals:
    turns: int = 0
    turn_errors: int = 0
    calls: int = 0
    errors: int = 0
    approvals: int = 0
    #: Calls (and requests) the draft has no node for any more.
    unplaced: int = 0


@dataclass
class Traffic:
    since: datetime
    nodes: dict[str, NodeCount] = field(default_factory=dict)
    total: Totals = field(default_factory=Totals)


def node_ids(graph: Graph, name: str, tool_input: Any, mcp_ids: dict[str, str]) -> list[str]:
    """The canvas nodes one tool call reached."""
    return trace_nodes.nodes_for_tool(graph, name, tool_input, mcp_ids)


def _graph(assistant: Assistant) -> Graph:
    try:
        return Graph.model_validate(assistant.draft_graph or {})
    except ValidationError:
        # A draft that no longer reads still answers with totals.
        return Graph()


def _live_conversation() -> Any:
    return (Conversation.mode == ConversationMode.live) & Conversation.eval_run_id.is_(None)


async def for_assistant(
    session: AsyncSession, assistant: Assistant, *, hours: int, now: datetime | None = None
) -> Traffic:
    since = (now or datetime.now(UTC)) - timedelta(hours=hours)
    graph = _graph(assistant)
    servers = await session.execute(
        select(McpServer.name, McpServer.id).where(McpServer.assistant_id == assistant.id)
    )
    mcp_ids = {name: str(i) for name, i in servers.all()}
    out = Traffic(since=since)

    def place(targets: list[str]) -> list[NodeCount]:
        if not targets:
            out.total.unplaced += 1
        return [out.nodes.setdefault(t, NodeCount()) for t in targets]

    calls = (
        await session.execute(
            select(ToolCall.call_id, ToolCall.tool_name, ToolCall.input, ToolCall.status)
            .join(Conversation, Conversation.id == ToolCall.conversation_id)
            .where(
                Conversation.assistant_id == assistant.id,
                _live_conversation(),
                ToolCall.created_at >= since,
            )
        )
    ).all()
    inputs: dict[str, Any] = {}
    for call_id, name, args, status in calls:
        failed = status == "error"
        out.total.calls += 1
        out.total.errors += int(failed)
        for n in place(node_ids(graph, name, args, mcp_ids)):
            n.calls += 1
            n.errors += int(failed)
        if call_id:
            inputs[str(call_id)] = args

    # A request outlives its conversation (SET NULL), so one without a
    # conversation still counts; `preview` keeps builders' tests out.
    approvals = (
        await session.execute(
            select(Approval.tool_name, Approval.input, Approval.tool_call_id)
            .outerjoin(Conversation, Conversation.id == Approval.conversation_id)
            .where(
                Approval.assistant_id == assistant.id,
                Approval.preview.is_(False),
                Approval.created_at >= since,
                or_(Approval.conversation_id.is_(None), Conversation.eval_run_id.is_(None)),
            )
        )
    ).all()
    for name, shown, call_id in approvals:
        out.total.approvals += 1
        args = inputs.get(str(call_id)) if call_id else None
        for n in place(node_ids(graph, name, args or shown, mcp_ids)):
            n.approvals += 1

    runs = (
        await session.execute(
            select(Run.status)
            .join(Conversation, Conversation.id == Run.conversation_id)
            .where(
                Conversation.assistant_id == assistant.id,
                _live_conversation(),
                Run.created_at >= since,
            )
        )
    ).all()
    out.total.turns = len(runs)
    out.total.turn_errors = sum(1 for (status,) in runs if status == RunStatus.error)
    for node in graph.nodes:
        if node.type == "agent" and out.total.turns:
            agent = out.nodes.setdefault(node.id, NodeCount())
            agent.calls += out.total.turns
            agent.errors += out.total.turn_errors
    return out


__all__ = ["MAX_HOURS", "NodeCount", "Totals", "Traffic", "for_assistant", "node_ids"]
