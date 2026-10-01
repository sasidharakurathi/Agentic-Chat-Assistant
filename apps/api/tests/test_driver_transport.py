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
import json
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
from tests.scripted_cli import SESSION, Script, ScriptedCLI
from tests.scripted_cli import assistant as _assistant
from tests.scripted_cli import delta as _delta
from tests.scripted_cli import result as _result
from tests.scripted_cli import tool_result as _tool_result

pytestmark = pytest.mark.anyio

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
