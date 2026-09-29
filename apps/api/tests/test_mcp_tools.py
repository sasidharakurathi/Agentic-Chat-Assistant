"""MCP tools in a chat turn (task 4.6).

The end-to-end cases run a real turn (the offline FakeDriver, which calls
tools through the real permission callback) against a real stdio MCP server
behind a real runner: registered and discovered through the API, allowed in
the draft config, called in chat, recorded in the audit table.
"""

from __future__ import annotations

import json
import sys
import uuid
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from app.agent.caps_mcp import build_mcp_toolset, render_result
from app.agent.options import build_claude_options, build_runtime_spec, permitted_tool_names
from app.config import settings
from app.db.session import get_sessionmaker
from app.graph.compile import compile_graph
from app.graph.nodes import Graph
from app.graph.project import project_config
from app.models.conversation import ToolCall
from app.models.integration import McpServer
from app.schemas.assistant_config import AssistantConfig
from httpx import AsyncClient
from pydantic import ValidationError
from sqlalchemy import select

pytestmark = pytest.mark.anyio

ECHO = Path(__file__).resolve().parent / "fixtures" / "mcp_echo_server.py"
RUNNER_TOKEN = "runner-test-token-0123456789"  # as in conftest.py
CATALOG = [
    {"name": "echo", "description": "Return the text.", "input_schema": {"type": "object"}},
    {"name": "environment", "description": "", "input_schema": {"type": "object"}},
    {
        "name": "lookup",
        "description": "Read.",
        "input_schema": {"type": "object"},
        "read_only": True,
    },
]


@pytest.fixture
def use_runner(runner: str, monkeypatch: pytest.MonkeyPatch) -> str:
    monkeypatch.setattr(settings, "mcp_runner_url", runner)
    monkeypatch.setattr(settings, "mcp_runner_token", RUNNER_TOKEN)
    return runner


async def _assistant(client: AsyncClient, headers: dict[str, str]) -> dict[str, Any]:
    r = await client.post("/api/v1/assistants", json={"name": "MCP"}, headers=headers)
    return dict(r.json())


async def _echo_server(client: AsyncClient, headers: dict[str, str], aid: str) -> str:
    r = await client.post(
        f"/api/v1/assistants/{aid}/mcp-servers",
        json={"name": "echo", "transport": "stdio", "command": sys.executable, "args": [str(ECHO)]},
        headers=headers,
    )
    assert r.status_code == 201, r.text
    sid = str(r.json()["id"])
    found = await client.post(
        f"/api/v1/assistants/{aid}/mcp-servers/{sid}:discover-tools", headers=headers
    )
    assert found.json()["ok"] is True, found.text
    return sid


async def _allow(
    client: AsyncClient,
    headers: dict[str, str],
    assistant: dict[str, Any],
    sid: str,
    tools: list[str],
    mcp_default: str = "auto",
) -> None:
    cfg = dict(assistant["draft_config"])
    cfg["mcp_servers"] = [{"id": sid, "tools": tools}]
    cfg["approval_policy"] = {**cfg["approval_policy"], "mcp_default": mcp_default}
    r = await client.put(
        f"/api/v1/assistants/{assistant['id']}/draft-config", json=cfg, headers=headers
    )
    assert r.status_code == 200, r.text


async def _chat(client: AsyncClient, headers: dict[str, str], aid: str, text: str) -> list[dict]:
    conv = await client.post(f"/api/v1/assistants/{aid}/conversations", json={}, headers=headers)
    events: list[dict] = []
    async with client.stream(
        "POST",
        f"/api/v1/conversations/{conv.json()['id']}/messages",
        json={"text": text},
        headers=headers,
    ) as resp:
        assert resp.status_code == 200, resp.text
        async for line in resp.aiter_lines():
            if line.startswith("data: "):
                events.append(json.loads(line[6:]))
    return events


# ── config ───────────────────────────────────────────────────


def test_each_server_carries_its_allowlist_and_approval() -> None:
    cfg = AssistantConfig.model_validate(
        {
            "mcp_servers": [
                "22222222-2222-2222-2222-222222222222",
                {"id": "11111111-1111-1111-1111-111111111111", "tools": ["b", "a", "a"]},
            ]
        }
    )
    first, second = cfg.mcp_servers
    assert (first.id, first.tools, first.approval) == (
        "11111111-1111-1111-1111-111111111111",
        ["a", "b"],
        None,  # no server rule set: the assistant default applies (task 4.7)
    )
    assert second.tools == [], "an old bare id allows nothing"
    with pytest.raises(ValidationError):
        AssistantConfig.model_validate(
            {
                "mcp_servers": [
                    {"id": "11111111-1111-1111-1111-111111111111", "tools": ["no spaces"]}
                ]
            }
        )


def test_the_canvas_node_compiles_to_the_config_and_back() -> None:
    sid = "11111111-1111-1111-1111-111111111111"
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
                        "mcp_server_id": sid,
                        "tool_allowlist": ["search", "create"],
                        "approval": "auto",
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
    assert (ref.id, ref.tools, ref.approval) == (sid, ["create", "search"], "auto")
    back = project_config(cfg)
    node = next(n for n in back.nodes if n.type == "mcp_server")
    assert sorted(node.data.tool_allowlist) == ["create", "search"]  # type: ignore[union-attr]
    assert compile_graph(back) == cfg


async def test_a_bad_tool_name_on_the_canvas_is_an_error_not_a_crash(
    client: AsyncClient, org_headers: dict[str, str]
) -> None:
    a = await _assistant(client, org_headers)
    graph = {
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
                    "mcp_server_id": "11111111-1111-1111-1111-111111111111",
                    "tool_allowlist": ["rm -rf"],
                },
            },
        ],
        "edges": [
            {"source": "in", "target": "ag"},
            {"source": "ag", "target": "out"},
            {"source": "mcp", "target": "ag"},
        ],
    }
    r = await client.put(
        f"/api/v1/assistants/{a['id']}/draft-graph", json=graph, headers=org_headers
    )
    assert r.status_code == 200, r.text
    assert "invalid_mcp_tool" in {e["code"] for e in r.json()["validation"]["errors"]}


# ── which tools a turn gets ──────────────────────────────────


async def _server_row(aid: uuid.UUID, org_id: uuid.UUID, *, enabled: bool = True) -> uuid.UUID:
    async with get_sessionmaker()() as s:
        row = McpServer(
            assistant_id=aid,
            org_id=org_id,
            name=f"srv-{uuid.uuid4().hex[:6]}",
            transport="http",
            url="https://mcp.example.com/mcp",
            args=[],
            header_names=[],
            env_keys=[],
            enabled=enabled,
            tools=CATALOG,
            sandbox={},
        )
        s.add(row)
        await s.commit()
        return row.id


async def test_only_allowed_and_discovered_tools_are_offered(
    client: AsyncClient, org_headers: dict[str, str]
) -> None:
    a = await _assistant(client, org_headers)
    aid, org = uuid.UUID(a["id"]), uuid.UUID(a["org_id"])
    on = await _server_row(aid, org)
    off = await _server_row(aid, org, enabled=False)
    other = await _assistant(client, org_headers)
    foreign = await _server_row(uuid.UUID(other["id"]), org)

    cfg = AssistantConfig.model_validate(
        {
            "mcp_servers": [
                {"id": str(on), "tools": ["echo", "lookup", "gone"]},  # "gone" isn't listed
                {"id": str(off), "tools": ["echo"]},
                {"id": str(foreign), "tools": ["echo"]},
            ]
        }
    )
    toolset = await build_mcp_toolset(cfg, aid)
    assert sorted(t.name for t in toolset.tools) == ["echo", "lookup"]
    lookup = next(t for t in toolset.tools if t.name == "lookup")
    assert lookup.read_only is True and lookup.open_world is True
    assert lookup.qualified_name.startswith("mcp__srv-") and lookup.qualified_name.endswith(
        "__lookup"
    )

    spec = build_runtime_spec(cfg, assistant_id=aid, mcp_tools=toolset.tools)
    assert {t.qualified_name for t in toolset.tools} <= set(spec.enabled_tools)
    assert {t.qualified_name for t in toolset.tools} <= permitted_tool_names(spec)
    options = build_claude_options(spec, can_use_tool=None)  # type: ignore[arg-type]
    server_name = lookup.server
    assert set(options.mcp_servers) >= {server_name}
    assert options.allowed_tools == [], "MCP tools still go through approvals"


def test_results_are_rendered_for_the_model() -> None:
    text = SimpleNamespace(type="text", text="hello")
    image = SimpleNamespace(type="image", data="...", mime_type="image/png")
    out = render_result(SimpleNamespace(content=[text, image], is_error=False))
    assert out["content"][0]["text"] == "hello\n[image content not shown]"
    structured = render_result(SimpleNamespace(content=[], structured_content={"a": 1}))
    assert json.loads(structured["content"][0]["text"]) == {"a": 1}
    failed = render_result(SimpleNamespace(content=[text], is_error=True))
    assert failed["is_error"] is True


# ── a real turn ──────────────────────────────────────────────


async def test_a_turn_calls_an_mcp_tool_through_the_runner(
    client: AsyncClient, org_headers: dict[str, str], use_runner: str
) -> None:
    a = await _assistant(client, org_headers)
    sid = await _echo_server(client, org_headers, a["id"])
    await _allow(client, org_headers, a, sid, ["echo"])

    events = await _chat(client, org_headers, a["id"], 'mcp: echo.echo {"text": "from chat"}')
    call = next(e for e in events if e["type"] == "tool_call")
    result = next(e for e in events if e["type"] == "tool_result")
    assert call["name"] == "mcp__echo__echo"
    assert result["status"] == "success" and result["output"] == "from chat"

    async with get_sessionmaker()() as s:
        row = (
            await s.scalars(select(ToolCall).where(ToolCall.tool_name == "mcp__echo__echo"))
        ).one()
    assert row.server == "echo", "the audit row names the MCP server"

    # The connection (and the local command behind it) ended with the turn.
    async with httpx.AsyncClient() as http:
        assert (await http.get(f"{use_runner}/healthz")).json()["sessions"] == 0


async def test_a_tool_outside_the_allowlist_is_not_there(
    client: AsyncClient, org_headers: dict[str, str], use_runner: str
) -> None:
    a = await _assistant(client, org_headers)
    sid = await _echo_server(client, org_headers, a["id"])
    await _allow(client, org_headers, a, sid, ["echo"])
    events = await _chat(client, org_headers, a["id"], "mcp: echo.environment {}")
    assert not any(e["type"] == "tool_call" for e in events), "environment was never allowed"


async def test_the_policy_can_forbid_mcp_tools(
    client: AsyncClient, org_headers: dict[str, str], use_runner: str
) -> None:
    a = await _assistant(client, org_headers)
    sid = await _echo_server(client, org_headers, a["id"])
    await _allow(client, org_headers, a, sid, ["echo"], mcp_default="deny")
    events = await _chat(client, org_headers, a["id"], 'mcp: echo.echo {"text": "x"}')
    result = next(e for e in events if e["type"] == "tool_result")
    assert result["status"] == "denied"


async def test_an_unavailable_server_is_a_tool_error_not_a_broken_turn(
    client: AsyncClient,
    org_headers: dict[str, str],
    use_runner: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    a = await _assistant(client, org_headers)
    sid = await _echo_server(client, org_headers, a["id"])
    await _allow(client, org_headers, a, sid, ["echo"])
    monkeypatch.setattr(settings, "mcp_runner_url", "http://127.0.0.1:9")  # the runner goes away
    events = await _chat(client, org_headers, a["id"], 'mcp: echo.echo {"text": "x"}')
    result = next(e for e in events if e["type"] == "tool_result")
    assert result["status"] == "error"
    assert "The MCP server 'echo' is unavailable" in result["output"]
    assert events[-1]["type"] == "done"


async def test_auto_runs_read_only_tools_only(
    client: AsyncClient, org_headers: dict[str, str], use_runner: str
) -> None:
    """`auto` skips the human only for a tool the server declares read-only
    (plan §7.2). `echo` is declared read-only and just ran in the test above;
    `environment` is not, so even under `auto` it waits for a person, and
    when the person declines, it never runs."""
    import asyncio

    from app.services import chat as chat_svc

    a = await _assistant(client, org_headers)
    sid = await _echo_server(client, org_headers, a["id"])
    await _allow(client, org_headers, a, sid, ["echo", "environment"], mcp_default="auto")
    conv = await client.post(
        f"/api/v1/assistants/{a['id']}/conversations", json={}, headers=org_headers
    )
    events: list[Any] = []

    async def turn() -> None:
        async with get_sessionmaker()() as session:
            async for event in chat_svc.run_message(
                session,
                conversation_id=uuid.UUID(conv.json()["id"]),
                text="mcp: echo.environment {}",
            ):
                events.append(event)

    task = asyncio.create_task(turn())
    prompt = None
    for _ in range(500):
        prompt = next((e for e in events if e.type == "approval_required"), None)
        if prompt is not None or task.done():
            break
        await asyncio.sleep(0.02)
    assert prompt is not None, f"a tool not declared read-only ran unasked: {events}"
    assert prompt.rationale.startswith("echo: environment(")
    await client.post(
        f"/api/v1/approvals/{prompt.approval_id}:resolve",
        json={"decision": "denied"},
        headers=org_headers,
    )
    await asyncio.wait_for(task, 30)
    result = next(e for e in events if e.type == "tool_result")
    assert result.status == "denied"
