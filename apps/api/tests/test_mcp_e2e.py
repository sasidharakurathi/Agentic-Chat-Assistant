"""MCP end to end, through the real agent SDK client (task 4.9).

The other MCP tests call our proxy tools directly (the fake driver does).
This one proves the path a real turn takes, with nothing faked but the
model:

    registered server (API) -> discovered tools (API, through the runner)
      -> allowlist + rules (draft config)
      -> ClaudeSDKDriver -> the real claude_agent_sdk client
      -> an in-process SDK MCP server named "echo" (what the CLI talks to)
      -> our proxy -> the MCP runner -> the stdio echo server

The CLI's side is played by `ScriptedCLI` (tests/test_driver_transport.py),
which speaks the bundled CLI's control protocol: the MCP handshake and
`tools/list` on the in-process server, the PreToolUse hook, `can_use_tool`,
and `tools/call`. Two calls: `echo` (read-only, rule `auto`) must run without
anyone being asked; `environment` must reach a person, who declines, and
must not run.
"""

from __future__ import annotations

import sys
import uuid
from pathlib import Path
from typing import Any

import httpx
import pytest
from app.agent import driver as driver_mod
from app.agent.approvals import ApprovalRequest, build_can_use_tool
from app.agent.caps_mcp import build_mcp_toolset
from app.agent.driver import ClaudeSDKDriver
from app.agent.events import ToolCallEvent, ToolResultEvent
from app.agent.options import build_runtime_spec
from app.config import settings
from app.schemas.assistant_config import AssistantConfig
from claude_agent_sdk import ClaudeAgentOptions
from httpx import AsyncClient

from test_driver_transport import (  # type: ignore[import-not-found]
    ScriptedCLI,
    _assistant,
    _result,
    _tool_result,
)

pytestmark = pytest.mark.anyio

ECHO = Path(__file__).resolve().parent / "fixtures" / "mcp_echo_server.py"
RUNNER_TOKEN = "runner-test-token-0123456789"  # as in conftest.py


async def _hooks_allow(cli: ScriptedCLI, tool_use_id: str, name: str, call: dict) -> None:
    for callback_id in cli.hook_ids("PreToolUse"):
        verdict = await cli.request(
            "hook_callback",
            callback_id=callback_id,
            tool_use_id=tool_use_id,
            input={"hook_event_name": "PreToolUse", "tool_name": name, "tool_input": call},
        )
        assert "deny" not in str(verdict), verdict


SEEN: dict[str, Any] = {}


async def _turn(cli: ScriptedCLI) -> None:
    """The CLI's side of a turn with two MCP tool calls."""
    # What the model is offered: the in-process "echo" server's tools.
    SEEN["tools"] = await cli.mcp_connect("echo")
    cli.send({"type": "system", "subtype": "init", "session_id": "s-e2e", "tools": []})

    # 1. echo: read-only, rule auto -> allowed without a person, and it runs.
    call = {"text": "through the real client"}
    cli.send(
        _assistant(
            "m1",
            [{"type": "tool_use", "id": "toolu_1", "name": "mcp__echo__echo", "input": call}],
            40,
            5,
        )
    )
    await _hooks_allow(cli, "toolu_1", "mcp__echo__echo", call)
    permission = await cli.request(
        "can_use_tool",
        tool_name="mcp__echo__echo",
        input=call,
        tool_use_id="toolu_1",
        permission_suggestions=[],
    )
    SEEN["echo_permission"] = permission
    result = await cli.mcp("echo", "tools/call", {"name": "echo", "arguments": call})
    SEEN["echo_result"] = result
    cli.send(_tool_result("toolu_1", result["content"][0]["text"]))

    # 2. environment: not read-only -> a person is asked, declines, it never runs.
    cli.send(
        _assistant(
            "m2",
            [{"type": "tool_use", "id": "toolu_2", "name": "mcp__echo__environment", "input": {}}],
            50,
            5,
        )
    )
    await _hooks_allow(cli, "toolu_2", "mcp__echo__environment", {})
    denied = await cli.request(
        "can_use_tool",
        tool_name="mcp__echo__environment",
        input={},
        tool_use_id="toolu_2",
        permission_suggestions=[],
    )
    SEEN["environment_permission"] = denied
    cli.send(_tool_result("toolu_2", denied.get("message", "denied"), is_error=True))
    cli.send(_result(90, 10, 0.001))
    cli.end()


async def test_an_mcp_tool_runs_through_the_real_sdk_client_with_approvals(
    client: AsyncClient,
    org_headers: dict[str, str],
    runner: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "mcp_runner_url", runner)
    monkeypatch.setattr(settings, "mcp_runner_token", RUNNER_TOKEN)

    # Register and discover through the API, as a builder would.
    a = (await client.post("/api/v1/assistants", json={"name": "E2E"}, headers=org_headers)).json()
    sid = (
        await client.post(
            f"/api/v1/assistants/{a['id']}/mcp-servers",
            json={
                "name": "echo",
                "transport": "stdio",
                "command": sys.executable,
                "args": [str(ECHO)],
            },
            headers=org_headers,
        )
    ).json()["id"]
    found = await client.post(
        f"/api/v1/assistants/{a['id']}/mcp-servers/{sid}:discover-tools", headers=org_headers
    )
    assert found.json()["tool_count"] == 3, found.text

    config = AssistantConfig.model_validate(
        {
            "mcp_servers": [
                {"id": sid, "tools": ["echo", "environment"], "tool_approvals": {"echo": "auto"}}
            ]
        }
    )
    toolset = await build_mcp_toolset(config, uuid.UUID(a["id"]))
    spec = build_runtime_spec(config, assistant_id=uuid.UUID(a["id"]), mcp_tools=toolset.tools)

    asked: list[str] = []

    async def requester(tool_name: str, _input: dict, _risk: str, _why: str, **_: Any) -> str:
        asked.append(tool_name)
        return "denied"

    permissions: dict[str, str] = {}
    can_use_tool = build_can_use_tool(
        config.approval_policy,
        ApprovalRequest(conversation_id=uuid.uuid4(), org_id=uuid.uuid4(), request=requester),
        read_only_mcp=frozenset(t.qualified_name for t in toolset.tools if t.read_only),
        mcp_modes=toolset.modes,
        permissions=permissions,
    )

    made: list[ScriptedCLI] = []

    def factory(options: ClaudeAgentOptions) -> ScriptedCLI:
        made.append(ScriptedCLI(options, _turn))
        return made[-1]

    monkeypatch.setattr(driver_mod, "transport_factory", factory)
    SEEN.clear()
    try:
        events = [
            e
            async for e in ClaudeSDKDriver().stream(
                prompt="use the echo tools",
                spec=spec,
                policy=config.approval_policy,
                can_use_tool=can_use_tool,
            )
        ]
    finally:
        await toolset.aclose()

    (cli,) = made
    # The CLI was given an in-process server per MCP server, never a URL or
    # a command to run itself, and nothing is auto-approved at the SDK level.
    assert "echo" in cli.options.mcp_servers
    assert cli.options.mcp_servers["echo"]["type"] == "sdk"
    assert cli.options.allowed_tools == []

    # The model was offered exactly the allowed tools, with the server's schemas.
    offered = {t["name"]: t for t in SEEN["tools"]}
    assert sorted(offered) == ["echo", "environment"], "spin was never allowed"
    assert "text" in offered["echo"]["inputSchema"]["properties"]

    # echo ran without anyone being asked, and really reached the server.
    assert SEEN["echo_permission"]["behavior"] == "allow"
    assert SEEN["echo_result"]["content"][0]["text"] == "through the real client"
    # environment reached a person, who declined; it did not run.
    assert SEEN["environment_permission"]["behavior"] == "deny"
    assert asked == ["mcp__echo__environment"]
    assert permissions == {"toolu_1": "auto", "toolu_2": "declined"}

    calls = [e.name for e in events if isinstance(e, ToolCallEvent)]
    results = {e.id: e.status for e in events if isinstance(e, ToolResultEvent)}
    assert calls == ["mcp__echo__echo", "mcp__echo__environment"]
    assert results == {"toolu_1": "success", "toolu_2": "denied"}

    # And the runner has nothing left running.
    async with httpx.AsyncClient() as http:
        assert (await http.get(f"{runner}/healthz")).json()["sessions"] == 0
