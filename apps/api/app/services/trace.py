"""A run's trace, step by step (task 5.9, plan §9's run-trace drawer).

Built on demand from what the turn already saved:

- the answer's blocks: each tool call (name, input, output, status, how it
  was allowed, when it started and how long it took, a subagent's notes)
  and each guardrail finding;
- the `tool_calls` rows, for durations of runs saved before blocks carried
  them;
- the `approvals` a call waited on (matched by the call's id), with who
  answered and after how long;
- the graph, to name the canvas nodes each step touched
  (`graph/trace_nodes.py`): the published version's snapshot when a version
  answered, else the current draft.
"""

from __future__ import annotations

import uuid
from typing import Any, Literal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.graph import trace_nodes
from app.graph.nodes import Graph
from app.models.approval import Approval
from app.models.assistant import Assistant, AssistantVersion
from app.models.conversation import Conversation, Message, Run, ToolCall
from app.models.integration import McpServer
from app.models.user import User
from app.schemas.conversation import TraceApproval, TraceGuardrail, TraceStep

#: How much of a step's output the trace carries; the answer keeps it all.
OUTPUT_PREVIEW_CHARS = 2_000


async def _graph(
    session: AsyncSession, assistant_id: uuid.UUID, version_number: int | None
) -> tuple[Graph, Literal["version", "draft"]]:
    if version_number is not None:
        snapshot = await session.scalar(
            select(AssistantVersion.graph).where(
                AssistantVersion.assistant_id == assistant_id,
                AssistantVersion.version_number == version_number,
            )
        )
        if snapshot:
            return Graph.model_validate(snapshot), "version"
    assistant = await session.get(Assistant, assistant_id)
    return Graph.model_validate(assistant.draft_graph if assistant else {}), "draft"


async def _approvals(
    session: AsyncSession, conversation_id: uuid.UUID, call_ids: list[str]
) -> dict[str, TraceApproval]:
    if not call_ids:
        return {}
    rows = (
        await session.execute(
            select(Approval, User.name, User.email)
            .outerjoin(User, User.id == Approval.decided_by)
            .where(
                Approval.conversation_id == conversation_id,
                Approval.tool_call_id.in_(call_ids),
            )
        )
    ).all()
    out: dict[str, TraceApproval] = {}
    for approval, name, email in rows:
        waited = (
            int((approval.decided_at - approval.created_at).total_seconds() * 1000)
            if approval.decided_at
            else None
        )
        out[str(approval.tool_call_id)] = TraceApproval(
            status=approval.status.value,
            risk=approval.risk.value,
            requested_at=approval.created_at,
            decided_at=approval.decided_at,
            wait_ms=waited,
            decided_by=(name or email) if approval.decided_by else None,
        )
    return out


def _preview(output: Any) -> str | None:
    if output is None:
        return None
    text = str(output)
    return text if len(text) <= OUTPUT_PREVIEW_CHARS else text[:OUTPUT_PREVIEW_CHARS] + "…"


async def build(
    session: AsyncSession, run: Run, message: Message | None, conv: Conversation
) -> tuple[list[TraceStep], list[str], Literal["version", "draft"]]:
    """The timeline, every node the run touched, and which graph they're from."""
    graph, source = await _graph(session, conv.assistant_id, run.version_number)
    blocks = [b for b in (message.blocks or []) if isinstance(b, dict)] if message else []
    calls = [b for b in blocks if b.get("type", "tool_call") == "tool_call"]
    findings = [b for b in blocks if b.get("type") == "guardrail"]

    call_ids = [str(c["id"]) for c in calls if c.get("id")]
    approvals = await _approvals(session, conv.id, call_ids)
    latency: dict[str, int] = {}
    if message is not None:
        rows = await session.execute(
            select(ToolCall.call_id, ToolCall.latency_ms).where(ToolCall.message_id == message.id)
        )
        latency = {str(i): ms for i, ms in rows.all() if i and ms is not None}
    servers = await session.execute(
        select(McpServer.name, McpServer.id).where(McpServer.assistant_id == conv.assistant_id)
    )
    mcp_ids = {name: str(i) for name, i in servers.all()}

    steps: list[TraceStep] = []
    for c in calls:
        cid = str(c.get("id")) if c.get("id") else None
        name = str(c.get("name", ""))
        steps.append(
            TraceStep(
                id=cid,
                kind="tool",
                name=name,
                parent_id=c.get("parent_id"),
                started_ms=c.get("started_ms"),
                duration_ms=c.get("duration_ms", latency.get(cid or "")),
                status=c.get("status"),
                permission=c.get("permission"),
                input=c.get("input"),
                output=_preview(c.get("output")),
                subagent_text=c.get("subagent_text"),
                approval=approvals.get(cid or ""),
                nodes=trace_nodes.nodes_for_tool(graph, name, c.get("input"), mcp_ids),
            )
        )
    # In the order they ran; a run saved before start times keeps its order.
    steps.sort(key=lambda s: s.started_ms if s.started_ms is not None else 0)

    guard = trace_nodes.guardrail_nodes(graph)
    for f in findings:
        finding = TraceStep(
            kind="guardrail",
            name=str(f.get("tool") or f.get("check", "guardrail")),
            guardrail=TraceGuardrail(
                check=str(f.get("check", "")),
                where=str(f.get("where", "")),
                detail=str(f.get("detail", "")),
            ),
            nodes=guard,
        )
        if f.get("where") == "user_message":
            # Checked before anything ran.
            steps.insert(0, finding)
            continue
        # After the last call of the tool it was about, else at the end.
        after = max(
            (i for i, s in enumerate(steps) if s.kind == "tool" and s.name == f.get("tool")),
            default=len(steps) - 1,
        )
        steps.insert(after + 1, finding)

    touched = list(trace_nodes.always_nodes(graph))
    for s in steps:
        touched.extend(n for n in s.nodes if n not in touched)
    return steps, touched, source


__all__ = ["OUTPUT_PREVIEW_CHARS", "build"]
