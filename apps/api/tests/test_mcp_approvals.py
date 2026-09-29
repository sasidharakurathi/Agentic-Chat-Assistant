"""Per-tool approval for MCP tools, and the audit of every call (task 4.7).

The end-to-end cases run real turns against the echo server behind a real
runner. `echo` is declared read-only by the server; `environment` is not.
"""

from __future__ import annotations

import asyncio
import json
import sys
import uuid
from pathlib import Path
from typing import Any

import pytest
from app.agent.approvals import classify, mcp_mode, redact
from app.config import settings
from app.db.session import get_sessionmaker
from app.graph.compile import compile_graph
from app.graph.nodes import Graph
from app.graph.project import project_config
from app.models.conversation import ToolCall
from app.schemas.assistant_config import ApprovalPolicy, McpServerRef
from app.services import chat as chat_svc
from httpx import AsyncClient
from sqlalchemy import select

pytestmark = pytest.mark.anyio

ECHO = Path(__file__).resolve().parent / "fixtures" / "mcp_echo_server.py"
RUNNER_TOKEN = "runner-test-token-0123456789"  # as in conftest.py
SID = "11111111-1111-1111-1111-111111111111"


# ── the rule ─────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("default", "server", "tools", "expected"),
    [
        ("require", None, {}, "require"),  # nothing set: the assistant default
        ("require", "auto", {}, "auto"),  # the server's rule
        ("require", "auto", {"t": "require"}, "require"),  # the tool's rule wins
        ("auto", None, {"t": "deny"}, "deny"),
        ("deny", "auto", {"t": "auto"}, "deny"),  # the switch-off wins over everything
    ],
)
def test_the_most_specific_rule_applies(
    default: str, server: str | None, tools: dict[str, str], expected: str
) -> None:
    ref = McpServerRef(id=SID, tools=["t"], approval=server, tool_approvals=tools)  # type: ignore[arg-type]
    policy = ApprovalPolicy(mcp_default=default)  # type: ignore[arg-type]
    assert mcp_mode(policy, ref, "t") == expected


def test_auto_only_skips_the_human_for_read_only_tools() -> None:
    policy = ApprovalPolicy()
    modes = {"mcp__s__read": "auto", "mcp__s__write": "auto"}
    read_only = frozenset({"mcp__s__read"})
    assert classify("mcp__s__read", {}, policy, read_only_mcp=read_only, mcp_modes=modes) == (
        "auto",
        "low",
    )
    # `auto` on a tool the server does not declare read-only is medium risk,
    # which the router never runs unasked.
    assert classify("mcp__s__write", {}, policy, read_only_mcp=read_only, mcp_modes=modes) == (
        "auto",
        "medium",
    )


def test_per_tool_rules_round_trip_through_the_canvas() -> None:
    graph = Graph.model_validate(
        {
            "schema_version": 1,
            "nodes": [
                {"id": "in", "type": "input", "position": {"x": 0, "y": 0}, "data": {}},
                {"id": "ag", "type": "agent", "position": {"x": 200, "y": 0}, "data": {}},
                {"id": "out", "type": "output", "position": {"x": 400, "y": 0}, "data": {}},
                {
                    "id": "mcp",
                    "type": "mcp_server",
                    "position": {"x": 0, "y": 150},
                    "data": {
                        "mcp_server_id": SID,
                        "tool_allowlist": ["read", "write"],
                        "tool_approvals": {"write": "deny", "read": "auto"},
                    },
                },
            ],
            "edges": [
                {"source": "in", "target": "ag"},
                {"source": "ag", "target": "out"},
                {"source": "mcp", "target": "ag"},
            ],
        }
    )
    cfg = compile_graph(graph)
    (ref,) = cfg.mcp_servers
    assert ref.approval is None, "no server rule set: the assistant default applies"
    assert ref.tool_approvals == {"read": "auto", "write": "deny"}
    assert compile_graph(project_config(cfg)) == cfg


def test_secret_shaped_values_never_reach_the_audit_or_the_card() -> None:
    token = "ghp_" + "a" * 36
    shown = redact({"query": f"repo:x token {token}", "nested": [token], "authorization": "x"})
    assert token not in json.dumps(shown)
    assert shown["authorization"] == "<redacted>"


# ── real turns ───────────────────────────────────────────────


@pytest.fixture
def use_runner(runner: str, monkeypatch: pytest.MonkeyPatch) -> str:
    monkeypatch.setattr(settings, "mcp_runner_url", runner)
    monkeypatch.setattr(settings, "mcp_runner_token", RUNNER_TOKEN)
    return runner


async def _setup(
    client: AsyncClient, headers: dict[str, str], *, ref: dict[str, Any], default: str
) -> tuple[str, str]:
    a = (await client.post("/api/v1/assistants", json={"name": "MCP"}, headers=headers)).json()
    r = await client.post(
        f"/api/v1/assistants/{a['id']}/mcp-servers",
        json={"name": "echo", "transport": "stdio", "command": sys.executable, "args": [str(ECHO)]},
        headers=headers,
    )
    sid = r.json()["id"]
    await client.post(
        f"/api/v1/assistants/{a['id']}/mcp-servers/{sid}:discover-tools", headers=headers
    )
    cfg = dict(a["draft_config"])
    cfg["mcp_servers"] = [{"id": sid, "tools": ["echo", "environment"], **ref}]
    cfg["approval_policy"] = {**cfg["approval_policy"], "mcp_default": default}
    put = await client.put(f"/api/v1/assistants/{a['id']}/draft-config", json=cfg, headers=headers)
    assert put.status_code == 200, put.text
    conv = await client.post(
        f"/api/v1/assistants/{a['id']}/conversations", json={}, headers=headers
    )
    return a["id"], conv.json()["id"]


async def _turn(conversation_id: str, text: str, events: list[Any]) -> None:
    async with get_sessionmaker()() as session:
        async for event in chat_svc.run_message(
            session, conversation_id=uuid.UUID(conversation_id), text=text
        ):
            events.append(event)


async def _audit(conversation_id: str) -> list[tuple[str, str | None, str | None]]:
    async with get_sessionmaker()() as s:
        rows = (
            await s.scalars(
                select(ToolCall).where(ToolCall.conversation_id == uuid.UUID(conversation_id))
            )
        ).all()
    return [(r.tool_name, r.status, r.permission) for r in rows]


async def test_a_read_only_tool_under_auto_runs_and_says_so(
    client: AsyncClient, org_headers: dict[str, str], use_runner: str
) -> None:
    _, cid = await _setup(
        client, org_headers, ref={"tool_approvals": {"echo": "auto"}}, default="require"
    )
    events: list[Any] = []
    await _turn(cid, 'mcp: echo.echo {"text": "hi"}', events)
    result = next(e for e in events if e.type == "tool_result")
    assert (result.status, result.permission) == ("success", "auto")
    assert await _audit(cid) == [("mcp__echo__echo", "success", "auto")]


async def test_a_tool_rule_can_forbid_one_tool(
    client: AsyncClient, org_headers: dict[str, str], use_runner: str
) -> None:
    _, cid = await _setup(
        client,
        org_headers,
        ref={"approval": "auto", "tool_approvals": {"environment": "deny"}},
        default="require",
    )
    events: list[Any] = []
    await _turn(cid, "mcp: echo.environment {}", events)
    result = next(e for e in events if e.type == "tool_result")
    assert (result.status, result.permission) == ("denied", "refused")
    assert await _audit(cid) == [("mcp__echo__environment", "denied", "refused")]


async def test_the_assistant_switch_off_beats_a_tool_rule(
    client: AsyncClient, org_headers: dict[str, str], use_runner: str
) -> None:
    _, cid = await _setup(
        client, org_headers, ref={"tool_approvals": {"echo": "auto"}}, default="deny"
    )
    events: list[Any] = []
    await _turn(cid, 'mcp: echo.echo {"text": "hi"}', events)
    result = next(e for e in events if e.type == "tool_result")
    assert result.permission == "refused"


@pytest.mark.parametrize(
    ("decision", "status", "permission"),
    [
        ("approved", "success", "approved"),
        ("denied", "denied", "declined"),
    ],
)
async def test_a_person_decides_and_the_audit_says_who_did(
    client: AsyncClient,
    org_headers: dict[str, str],
    use_runner: str,
    decision: str,
    status: str,
    permission: str,
) -> None:
    _, cid = await _setup(client, org_headers, ref={}, default="require")
    events: list[Any] = []
    task = asyncio.create_task(_turn(cid, "mcp: echo.environment {}", events))
    prompt = None
    for _ in range(500):
        prompt = next((e for e in events if e.type == "approval_required"), None)
        if prompt is not None or task.done():
            break
        await asyncio.sleep(0.02)
    assert prompt is not None, [e.type for e in events]
    r = await client.post(
        f"/api/v1/approvals/{prompt.approval_id}:resolve",
        json={"decision": decision},
        headers=org_headers,
    )
    assert r.status_code == 200, r.text
    await asyncio.wait_for(task, 60)
    result = next(e for e in events if e.type == "tool_result")
    assert (result.status, result.permission) == (status, permission)
    assert await _audit(cid) == [("mcp__echo__environment", status, permission)]


async def test_a_refusal_says_which_rule_and_where(
    client: AsyncClient, org_headers: dict[str, str], use_runner: str
) -> None:
    _, cid = await _setup(
        client, org_headers, ref={"tool_approvals": {"environment": "deny"}}, default="require"
    )
    events: list[Any] = []
    await _turn(cid, "mcp: echo.environment {}", events)
    result = next(e for e in events if e.type == "tool_result")
    assert "do not allow echo: environment" in result.output
    assert "MCP server node" in result.output
