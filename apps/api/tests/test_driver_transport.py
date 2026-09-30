"""`ClaudeSDKDriver` end to end against a scripted CLI (task 1.11).

`test_driver_claude.py` covers the message mapping as a pure function. This
covers everything around it that only ever ran against the real CLI: the
options the driver builds, the SDK client and its message parser, and the
control protocol the CLI drives it with:

- `initialize`, which carries the PreToolUse hook the driver registered;
- `hook_callback`, which runs that hook;
- `can_use_tool`, which runs the permission router (and records a denial);
- `mcp_message`, which calls a platform capability in-process;
- `interrupt`, which the driver sends when the user stops a turn.

The script plays the CLI's side: the same newline-delimited JSON the bundled
CLI writes, in the order it writes it for a turn with one tool call.
"""

from __future__ import annotations

import asyncio
import itertools
import json
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any

import pytest
from app.agent import driver as driver_mod
from app.agent.driver import ClaudeSDKDriver, FakeDriver, get_driver
from app.agent.events import (
    AgentEvent,
    ErrorEvent,
    TokenEvent,
    ToolCallEvent,
    ToolResultEvent,
    UsageEvent,
)
from app.agent.options import build_runtime_spec
from app.schemas.assistant_config import AssistantConfig
from claude_agent_sdk import ClaudeAgentOptions, ProcessError
from claude_agent_sdk._internal.transport import Transport

pytestmark = pytest.mark.anyio

SESSION = "sess-scripted"
Script = Callable[["ScriptedCLI"], Awaitable[None]]


class ScriptedCLI(Transport):
    """The CLI's end of the stdio protocol, driven by a script."""

    def __init__(self, options: ClaudeAgentOptions, script: Script) -> None:
        self.options = options
        self._script = script
        self._out: asyncio.Queue[dict[str, Any] | BaseException | None] = asyncio.Queue()
        self._pending: dict[str, asyncio.Future[dict[str, Any]]] = {}
        self._ids = itertools.count(1)
        self._task: asyncio.Task[None] | None = None
        self.initialize: dict[str, Any] = {}
        self.prompt: str | None = None
        self.interrupted = asyncio.Event()

    # ── Transport ────────────────────────────────────────────
    async def connect(self) -> None:
        return None

    def is_ready(self) -> bool:
        return True

    async def close(self) -> None:
        if self._task is not None:
            self._task.cancel()

    async def end_input(self) -> None:
        return None

    async def write(self, data: str) -> None:
        for line in data.splitlines():
            if line.strip():
                self._on_sdk_message(json.loads(line))

    async def read_messages(self) -> AsyncIterator[dict[str, Any]]:
        while True:
            item = await self._out.get()
            if item is None:
                return
            if isinstance(item, BaseException):
                raise item
            yield item

    # ── what the SDK sent ────────────────────────────────────
    def _on_sdk_message(self, msg: dict[str, Any]) -> None:
        kind = msg.get("type")
        if kind == "control_request":
            request = msg["request"]
            if request["subtype"] == "initialize":
                self.initialize = request
            elif request["subtype"] == "interrupt":
                self.interrupted.set()
            self.send(
                {
                    "type": "control_response",
                    "response": {
                        "subtype": "success",
                        "request_id": msg["request_id"],
                        "response": {},
                    },
                }
            )
        elif kind == "control_response":
            response = msg["response"]
            future = self._pending.pop(response["request_id"])
            if response["subtype"] == "success":
                future.set_result(response.get("response") or {})
            else:
                future.set_exception(RuntimeError(response.get("error")))
        elif kind == "user" and self.prompt is None:
            self.prompt = msg["message"]["content"]
            self._task = asyncio.create_task(self._run())

    async def _run(self) -> None:
        try:
            await self._script(self)
        except Exception as exc:
            self._out.put_nowait(exc)

    # ── what the script sends ────────────────────────────────
    def send(self, msg: dict[str, Any]) -> None:
        self._out.put_nowait(msg)

    def end(self) -> None:
        self._out.put_nowait(None)

    def fail(self, exc: BaseException) -> None:
        self._out.put_nowait(exc)

    async def request(self, subtype: str, **fields: Any) -> dict[str, Any]:
        """A control request from the CLI to the SDK, answered by the SDK."""
        request_id = f"cli-{next(self._ids)}"
        future: asyncio.Future[dict[str, Any]] = asyncio.get_running_loop().create_future()
        self._pending[request_id] = future
        self.send(
            {
                "type": "control_request",
                "request_id": request_id,
                "request": {"subtype": subtype, **fields},
            }
        )
        return await asyncio.wait_for(future, 5)

    async def mcp(
        self, server: str, method: str, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """One JSON-RPC message to an in-process MCP server, as the CLI's MCP
        client sends it. Notifications (no id) get an empty ack."""
        message: dict[str, Any] = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            message["params"] = params
        if not method.startswith("notifications/"):
            message["id"] = next(self._ids)
        reply = await self.request("mcp_message", server_name=server, message=message)
        response: dict[str, Any] = reply["mcp_response"]
        assert "error" not in response, response
        return response.get("result") or {}

    async def mcp_connect(self, server: str) -> list[dict[str, Any]]:
        """The handshake the CLI does before it can call a tool. Returns the
        server's tool list."""
        await self.mcp(
            server,
            "initialize",
            {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "scripted-cli", "version": "0"},
            },
        )
        await self.mcp(server, "notifications/initialized")
        tools: list[dict[str, Any]] = (await self.mcp(server, "tools/list"))["tools"]
        return tools

    def hook_ids(self, event: str) -> list[str]:
        matchers = (self.initialize.get("hooks") or {}).get(event) or []
        return [cid for m in matchers for cid in m["hookCallbackIds"]]


# ── CLI message builders (the shapes the bundled CLI emits) ────────


def _delta(message_id: str, text: str) -> list[dict[str, Any]]:
    base = {"uuid": f"u-{message_id}", "session_id": SESSION, "parent_tool_use_id": None}
    return [
        {
            **base,
            "type": "stream_event",
            "event": {"type": "message_start", "message": {"id": message_id}},
        },
        {
            **base,
            "type": "stream_event",
            "event": {"type": "content_block_delta", "delta": {"type": "text_delta", "text": text}},
        },
    ]


def _assistant(
    message_id: str, blocks: list[dict[str, Any]], tin: int, tout: int
) -> dict[str, Any]:
    return {
        "type": "assistant",
        "message": {
            "id": message_id,
            "model": "claude-sonnet-5",
            "content": blocks,
            "usage": {"input_tokens": tin, "output_tokens": tout},
        },
        "parent_tool_use_id": None,
        "session_id": SESSION,
    }


def _tool_result(tool_use_id: str, text: str, *, is_error: bool = False) -> dict[str, Any]:
    return {
        "type": "user",
        "message": {
            "role": "user",
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": tool_use_id,
                    "content": [{"type": "text", "text": text}],
                    "is_error": is_error,
                }
            ],
        },
        "parent_tool_use_id": None,
        "session_id": SESSION,
    }


def _result(tin: int, tout: int, cost: float, **extra: Any) -> dict[str, Any]:
    return {
        "type": "result",
        "subtype": "success",
        "duration_ms": 12,
        "duration_api_ms": 10,
        "is_error": False,
        "num_turns": 2,
        "session_id": SESSION,
        "total_cost_usd": cost,
        "usage": {"input_tokens": tin, "output_tokens": tout},
        **extra,
    }


# ── running the driver ─────────────────────────────────────────────


def _config(**tools: bool) -> AssistantConfig:
    return AssistantConfig.model_validate(
        {"tools": {name: {"enabled": on} for name, on in tools.items()}}
    )


async def _drive(
    monkeypatch: pytest.MonkeyPatch,
    script: Script,
    config: AssistantConfig,
    *,
    session_id: str | None = None,
    interrupt: asyncio.Event | None = None,
) -> tuple[list[AgentEvent], ScriptedCLI]:
    made: list[ScriptedCLI] = []

    def factory(options: ClaudeAgentOptions) -> ScriptedCLI:
        made.append(ScriptedCLI(options, script))
        return made[-1]

    monkeypatch.setattr(driver_mod, "transport_factory", factory)
    events = [
        e
        async for e in ClaudeSDKDriver().stream(
            prompt="what is 21 + 21?",
            spec=build_runtime_spec(config),
            policy=config.approval_policy,
            session_id=session_id,
            interrupt=interrupt,
        )
    ]
    (cli,) = made
    return events, cli


OFFERED: list[dict[str, Any]] = []


async def _one_tool_turn(cli: ScriptedCLI) -> None:
    """A turn with one calculator call, as the CLI runs it."""
    OFFERED[:] = await cli.mcp_connect("caps")
    cli.send({"type": "system", "subtype": "init", "session_id": SESSION, "tools": []})
    for m in _delta("msg_1", "Let me work that out."):
        cli.send(m)
    call = {"expression": "21 + 21"}
    cli.send(
        _assistant(
            "msg_1",
            [
                {"type": "text", "text": "Let me work that out."},
                {
                    "type": "tool_use",
                    "id": "toolu_1",
                    "name": "mcp__caps__calculator",
                    "input": call,
                },
            ],
            100,
            20,
        )
    )
    # The CLI runs the PreToolUse hooks, then asks for permission, then calls
    # the tool on the SDK's in-process MCP server.
    for callback_id in cli.hook_ids("PreToolUse"):
        verdict = await cli.request(
            "hook_callback",
            callback_id=callback_id,
            tool_use_id="toolu_1",
            input={
                "hook_event_name": "PreToolUse",
                "tool_name": "mcp__caps__calculator",
                "tool_input": call,
            },
        )
        assert "deny" not in json.dumps(verdict), verdict
    permission = await cli.request(
        "can_use_tool",
        tool_name="mcp__caps__calculator",
        input=call,
        tool_use_id="toolu_1",
        permission_suggestions=[],
    )
    assert permission["behavior"] == "allow", permission
    result = await cli.mcp(
        "caps", "tools/call", {"name": "calculator", "arguments": permission["updatedInput"]}
    )
    output = result["content"][0]["text"]
    cli.send(_tool_result("toolu_1", output))
    for m in _delta("msg_2", "It is 42."):
        cli.send(m)
    cli.send(_assistant("msg_2", [{"type": "text", "text": "It is 42."}], 150, 10))
    cli.send(_result(250, 30, 0.0021))
    cli.end()


async def test_a_tool_turn_runs_through_the_real_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events, cli = await _drive(monkeypatch, _one_tool_turn, _config(calculator=True))

    assert cli.prompt == "what is 21 + 21?"
    tokens = "".join(e.text for e in events if isinstance(e, TokenEvent))
    assert tokens == "Let me work that out.It is 42.", "streamed once, not repeated"
    (call,) = [e for e in events if isinstance(e, ToolCallEvent)]
    assert (call.id, call.name, call.input) == (
        "toolu_1",
        "mcp__caps__calculator",
        {"expression": "21 + 21"},
    )
    (result,) = [e for e in events if isinstance(e, ToolResultEvent)]
    assert result.status == "success" and "42" in result.output, "the capability really ran"

    usage = [e for e in events if isinstance(e, UsageEvent)]
    assert sum(u.tokens_in for u in usage) == 250
    assert sum(u.tokens_out for u in usage) == 30
    assert round(sum(u.cost_usd for u in usage), 6) == 0.0021, "settled to the CLI's total"
    assert sum(u.model_calls for u in usage) == 2
    assert usage[-1].sdk_session_id == SESSION
    assert not [e for e in events if isinstance(e, ErrorEvent)]


async def test_the_driver_registers_its_gate_and_tools_with_the_cli(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, cli = await _drive(monkeypatch, _one_tool_turn, _config(calculator=True))
    assert cli.hook_ids("PreToolUse"), "the PreToolUse gate reached initialize"
    assert "caps" in cli.options.mcp_servers
    # Nothing is auto-approved, so every call reaches `can_use_tool` (ADR 0003).
    assert cli.options.allowed_tools == []
    assert cli.options.can_use_tool is not None
    (calculator,) = [t for t in OFFERED if t["name"] == "calculator"]
    assert calculator["inputSchema"]["properties"]["expression"]["type"] == "string"
    assert calculator["annotations"]["readOnlyHint"] is True
    assert cli.options.include_partial_messages is True


async def test_a_resumed_conversation_passes_its_session(monkeypatch: pytest.MonkeyPatch) -> None:
    _, cli = await _drive(
        monkeypatch, _one_tool_turn, _config(calculator=True), session_id="sess-earlier"
    )
    assert cli.options.resume == "sess-earlier"


async def _denied_turn(cli: ScriptedCLI) -> None:
    call = {"command": "rm -rf /"}
    cli.send(
        _assistant(
            "msg_1", [{"type": "tool_use", "id": "toolu_9", "name": "Bash", "input": call}], 50, 5
        )
    )
    permission = await cli.request(
        "can_use_tool",
        tool_name="Bash",
        input=call,
        tool_use_id="toolu_9",
        permission_suggestions=[],
    )
    assert permission["behavior"] == "deny"
    cli.send(_tool_result("toolu_9", permission["message"], is_error=True))
    cli.send(_result(60, 8, 0.0003))
    cli.end()


async def test_a_refused_call_is_reported_as_denied_not_failed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events, _ = await _drive(monkeypatch, _denied_turn, _config())
    (result,) = [e for e in events if isinstance(e, ToolResultEvent)]
    assert result.id == "toolu_9" and result.status == "denied"


async def test_the_pre_tool_gate_denies_a_tool_that_is_not_enabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    verdicts: list[dict[str, Any]] = []

    async def script(cli: ScriptedCLI) -> None:
        call = {"expression": "1"}
        (callback_id,) = cli.hook_ids("PreToolUse")
        verdicts.append(
            await cli.request(
                "hook_callback",
                callback_id=callback_id,
                tool_use_id="toolu_2",
                input={
                    "hook_event_name": "PreToolUse",
                    "tool_name": "mcp__caps__calculator",
                    "tool_input": call,
                },
            )
        )
        cli.send(_result(1, 1, 0.0))
        cli.end()

    await _drive(monkeypatch, script, _config(datetime=True))
    (verdict,) = verdicts
    assert verdict["hookSpecificOutput"]["permissionDecision"] == "deny"


async def test_a_stop_is_sent_to_the_cli_as_an_interrupt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stop = asyncio.Event()

    async def script(cli: ScriptedCLI) -> None:
        for m in _delta("msg_1", "Working"):
            cli.send(m)
        stop.set()  # the user presses stop while text is streaming
        await asyncio.wait_for(cli.interrupted.wait(), 5)
        cli.send(
            _result(40, 3, 0.0004, subtype="error_during_execution", terminal_reason="interrupted")
        )
        cli.end()

    events, cli = await _drive(monkeypatch, script, _config(), interrupt=stop)
    assert cli.interrupted.is_set()
    usage = [e for e in events if isinstance(e, UsageEvent)]
    assert usage and usage[-1].terminal_reason == "interrupted", "the real usage still arrives"


@pytest.mark.parametrize(
    ("death", "code"),
    [
        # What the real transport raises when the CLI process dies.
        (ProcessError("Command failed", exit_code=1, stderr="at /srv/app: boom"), "agent_crashed"),
        (RuntimeError("CLI process exited with code 1"), "agent_error"),
    ],
)
async def test_a_cli_that_dies_mid_turn_is_a_typed_error(
    monkeypatch: pytest.MonkeyPatch, death: Exception, code: str
) -> None:
    """Typed since task 5.4, and the message is safe: it used to be
    `str(exc)`, stderr and all."""

    async def script(cli: ScriptedCLI) -> None:
        for m in _delta("msg_1", "Half an ans"):
            cli.send(m)
        cli.fail(death)

    events, _ = await _drive(monkeypatch, script, _config())
    assert isinstance(events[0], TokenEvent)
    error = events[-1]
    assert isinstance(error, ErrorEvent) and (error.code, error.retryable) == (code, True)
    assert "exited" not in error.message and "/srv/app" not in error.message


@pytest.mark.parametrize(
    ("choice", "env", "key", "expected"),
    [
        ("fake", "dev", "sk-set", FakeDriver),
        ("claude", "test", "", ClaudeSDKDriver),
        ("auto", "test", "sk-set", FakeDriver),
        ("auto", "dev", "", FakeDriver),
        ("auto", "dev", "sk-set", ClaudeSDKDriver),
    ],
)
def test_get_driver_picks_by_setting_env_and_key(
    monkeypatch: pytest.MonkeyPatch, choice: str, env: str, key: str, expected: type
) -> None:
    monkeypatch.setattr(driver_mod.settings, "agent_driver", choice)
    monkeypatch.setattr(driver_mod.settings, "app_env", env)
    monkeypatch.setattr(driver_mod.settings, "anthropic_api_key", key)
    assert type(get_driver()) is expected
