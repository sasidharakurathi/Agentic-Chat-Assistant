"""Traffic on the canvas (Phase 10.2).

Live tool calls, their errors and the approval requests they raised, placed
on the stations of the assistant's draft: the knowledge base, a database
(by its connection, and by the actions it declares), the HTTP request tool
(its declared actions), web search, an MCP server (by its name), a
subagent; the agent carries the turns. Previews, evals and calls outside
the window are not traffic; a call the draft has no station for is counted
as unplaced, never put on a guess.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from app.db.session import get_sessionmaker
from app.models.approval import Approval
from app.models.assistant import Assistant
from app.models.conversation import (
    Conversation,
    ConversationMode,
    Message,
    MessageRole,
    Run,
    RunStatus,
    ToolCall,
)
from app.models.integration import McpServer, McpTransport
from httpx import AsyncClient

from test_audiences import Org, _org  # type: ignore[import-not-found]

pytestmark = pytest.mark.anyio

API = "/api/v1"

CLOSE_TICKET = {
    "name": "close_ticket",
    "title": "Close a ticket",
    "sql": "UPDATE tickets SET status = 'closed' WHERE id = :id",
    "params": [{"name": "id", "type": "integer", "description": "The ticket"}],
    "tier": "approver",
}


@pytest.fixture
async def org(client: AsyncClient) -> Org:
    return await _org(client)


def _node(node_id: str, node_type: str, data: dict[str, Any] | None = None) -> dict[str, Any]:
    return {"id": node_id, "type": node_type, "position": {"x": 0, "y": 0}, "data": data or {}}


async def _drawn(client: AsyncClient, org: Org) -> tuple[str, str]:
    """An assistant whose draft has one station of each kind that takes
    traffic: its id, and its database's connection id."""
    r = await client.post(f"{API}/assistants", json={"name": "Helpdesk"}, headers=org.h("builder"))
    assert r.status_code == 201, r.text
    aid = str(r.json()["id"])
    cid = str(uuid.uuid4())
    async with get_sessionmaker()() as s:
        jira = McpServer(
            assistant_id=uuid.UUID(aid),
            org_id=uuid.UUID(org.id),
            name="jira",
            transport=McpTransport.http,
            url="https://jira.example.com/mcp",
        )
        s.add(jira)
        await s.flush()
        assistant = await s.get(Assistant, uuid.UUID(aid))
        assert assistant is not None
        assistant.draft_graph = {
            "schema_version": 1,
            "nodes": [
                _node("in", "input"),
                _node("agent", "agent"),
                _node("out", "output"),
                _node("kb", "knowledge_base"),
                _node("db", "database", {"connection_id": cid, "actions": [CLOSE_TICKET]}),
                _node("web", "tool", {"key": "web_search"}),
                _node(
                    "http",
                    "tool",
                    {"key": "http_request", "config": {"actions": [{"name": "open_ticket"}]}},
                ),
                _node("mcp", "mcp_server", {"mcp_server_id": str(jira.id)}),
                _node("sub", "subagent", {"role": "sql"}),
            ],
            "edges": [
                {"source": "in", "target": "agent"},
                {"source": "agent", "target": "out"},
                {"source": "kb", "target": "agent"},
            ],
        }
        await s.commit()
    return aid, cid


async def _conversation(
    org: Org, aid: str, *, mode: ConversationMode = ConversationMode.live
) -> tuple[uuid.UUID, uuid.UUID]:
    async with get_sessionmaker()() as s:
        conv = Conversation(
            assistant_id=uuid.UUID(aid),
            org_id=uuid.UUID(org.id),
            created_by=uuid.UUID(org.people["emp1"].user_id),
            mode=mode,
            last_message_at=datetime.now(UTC),
        )
        s.add(conv)
        await s.flush()
        answer = Message(
            conversation_id=conv.id, org_id=conv.org_id, role=MessageRole.assistant, content="ok"
        )
        s.add(answer)
        await s.commit()
        return conv.id, answer.id


async def _calls(
    org: Org,
    conv: uuid.UUID,
    message: uuid.UUID,
    calls: list[tuple[str, dict[str, Any], str]],
    *,
    at: datetime,
    call_ids: list[str | None] | None = None,
) -> None:
    async with get_sessionmaker()() as s:
        for i, (name, args, status) in enumerate(calls):
            s.add(
                ToolCall(
                    message_id=message,
                    conversation_id=conv,
                    org_id=uuid.UUID(org.id),
                    call_id=(call_ids or [None] * len(calls))[i],
                    tool_name=name,
                    server="caps",
                    input=args,
                    status=status,
                    created_at=at,
                )
            )
        await s.commit()


async def _approval(
    org: Org,
    aid: str,
    conv: uuid.UUID | None,
    tool: str,
    *,
    shown: dict[str, Any] | None = None,
    call_id: str | None = None,
    preview: bool = False,
) -> None:
    async with get_sessionmaker()() as s:
        s.add(
            Approval(
                conversation_id=conv,
                org_id=uuid.UUID(org.id),
                assistant_id=uuid.UUID(aid),
                preview=preview,
                tool_name=tool,
                tool_call_id=call_id,
                input=shown or {},
                created_at=datetime.now(UTC) - timedelta(hours=1),
            )
        )
        await s.commit()


async def _runs(org: Org, conv: uuid.UUID, statuses: list[RunStatus]) -> None:
    async with get_sessionmaker()() as s:
        for status in statuses:
            s.add(
                Run(
                    conversation_id=conv,
                    org_id=uuid.UUID(org.id),
                    status=status,
                    created_at=datetime.now(UTC) - timedelta(hours=1),
                )
            )
        await s.commit()


async def test_live_calls_errors_and_approvals_land_on_their_stations(
    client: AsyncClient, org: Org
) -> None:
    aid, cid = await _drawn(client, org)
    conv, message = await _conversation(org, aid)
    hour_ago = datetime.now(UTC) - timedelta(hours=1)
    await _calls(
        org,
        conv,
        message,
        [
            ("mcp__caps__kb_search", {"query": "vpn"}, "success"),
            ("mcp__caps__kb_search", {"query": "wifi"}, "success"),
            ("mcp__caps__kb_list_sources", {}, "error"),
            ("mcp__caps__sql_query", {"connection_id": cid, "sql": "SELECT 1"}, "success"),
            ("mcp__caps__act_close_ticket", {"id": 3}, "success"),
            ("mcp__caps__act_open_ticket", {"summary": "VPN"}, "success"),
            ("WebSearch", {"query": "status page"}, "success"),
            ("mcp__jira__create_issue", {"title": "VPN"}, "denied"),
            ("Agent", {"subagent_type": "sql", "prompt": "count tickets"}, "success"),
            # No calculator on the canvas any more: unplaced, not guessed.
            ("mcp__caps__calculator", {"expression": "1+1"}, "success"),
        ],
        at=hour_ago,
        call_ids=[None, None, None, None, "call-close", None, None, "call-jira", None, None],
    )
    # Outside the window.
    await _calls(
        org,
        conv,
        message,
        [("mcp__caps__kb_search", {}, "error")],
        at=datetime.now(UTC) - timedelta(hours=30),
    )
    await _approval(org, aid, conv, "mcp__caps__act_close_ticket", call_id="call-close")
    await _approval(org, aid, conv, "mcp__jira__create_issue", call_id="call-jira")
    # Its conversation since erased: placed by what the request shows.
    await _approval(
        org, aid, None, "mcp__caps__sql_query", shown={"connection_id": cid, "sql": "DELETE"}
    )
    await _runs(org, conv, [RunStatus.ok, RunStatus.ok, RunStatus.error])

    # A builder's preview is not traffic.
    preview, preview_msg = await _conversation(org, aid, mode=ConversationMode.preview)
    await _calls(org, preview, preview_msg, [("mcp__caps__kb_search", {}, "error")], at=hour_ago)
    await _approval(org, aid, preview, "mcp__caps__sql_query", preview=True)
    await _runs(org, preview, [RunStatus.error])

    r = await client.get(f"{API}/assistants/{aid}/traffic?hours=24", headers=org.h("builder"))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["hours"] == 24
    nodes = body["nodes"]
    assert nodes["kb"] == {"calls": 3, "errors": 1, "approvals": 0}
    assert nodes["db"] == {"calls": 2, "errors": 0, "approvals": 2}
    assert nodes["http"] == {"calls": 1, "errors": 0, "approvals": 0}
    assert nodes["web"] == {"calls": 1, "errors": 0, "approvals": 0}
    assert nodes["mcp"] == {"calls": 1, "errors": 0, "approvals": 1}, "declined is not an error"
    assert nodes["sub"] == {"calls": 1, "errors": 0, "approvals": 0}
    assert nodes["agent"] == {"calls": 3, "errors": 1, "approvals": 0}
    assert "in" not in nodes and "out" not in nodes
    assert body["total"] == {
        "turns": 3,
        "turn_errors": 1,
        "calls": 10,
        "errors": 1,
        "approvals": 3,
        "unplaced": 1,
    }


async def test_a_wider_window_reaches_further_back(client: AsyncClient, org: Org) -> None:
    aid, _ = await _drawn(client, org)
    conv, message = await _conversation(org, aid)
    await _calls(
        org,
        conv,
        message,
        [("mcp__caps__kb_search", {}, "success")],
        at=datetime.now(UTC) - timedelta(hours=30),
    )
    day = (await client.get(f"{API}/assistants/{aid}/traffic", headers=org.h("builder"))).json()
    assert day["nodes"] == {} and day["total"]["calls"] == 0
    week = (
        await client.get(f"{API}/assistants/{aid}/traffic?hours=168", headers=org.h("builder"))
    ).json()
    assert week["nodes"]["kb"]["calls"] == 1
    bad = await client.get(f"{API}/assistants/{aid}/traffic?hours=0", headers=org.h("builder"))
    assert bad.status_code == 422


async def test_whoever_opens_the_canvas_sees_its_traffic(client: AsyncClient, org: Org) -> None:
    aid, _ = await _drawn(client, org)
    url = f"{API}/assistants/{aid}/traffic"
    for who in ("builder", "builder2", "owner"):
        assert (await client.get(url, headers=org.h(who))).status_code == 200, who
    assert (await client.get(url, headers=org.h("emp1"))).status_code in (403, 404)
