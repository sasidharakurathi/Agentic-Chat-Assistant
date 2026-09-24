"""The SDK hooks for a turn (plan §4.2, `hooks = build_hooks(...)`; task 1.10).

Where each hook duty from the plan is actually enforced:

- **PreToolUse: deny anything not enabled.** A hook, below: it runs on every
  call, whatever the CLI's permission rules say, which `can_use_tool` does not.
- **PostToolUse: cap and strip secrets from outputs.** In
  `post_tool.run_capability`, which every platform capability passes through
  on its way back to the model. That is where the result is produced, so it
  needs no SDK hook contract to rewrite it, and the offline driver goes
  through the same code.
- **PostToolUse: record the call and its cost.** In `chat._finalize`: a row in
  `tool_calls` per call, with latency, and web searches on the usage ledger.
- **Stop: finalize the turn.** `chat.run_message` finalizes in a shielded
  block on every exit path, including a client disconnect, where a Stop hook
  would never run at all.

MCP servers users register (Phase 4) do not pass through
`run_capability`; their outputs will need a real PostToolUse hook here.
"""

from __future__ import annotations

from typing import Any

from claude_agent_sdk import HookMatcher
from claude_agent_sdk.types import HookEvent

from app.logging import get_logger

log = get_logger(__name__)


def build_tool_gate(permitted: frozenset[str]) -> Any:
    """A `PreToolUse` hook that denies any tool this assistant has not enabled.

    `can_use_tool` alone cannot be the final gate: the CLI only consults it for
    calls that would otherwise *prompt*, and some tools never prompt. A
    PreToolUse hook runs for every call regardless of permission rules, so
    this is where deny-by-default (ADR 0003) is actually enforced.

    For a permitted tool it returns no decision at all, so the normal flow
    continues and `can_use_tool` still classifies risk and waits for a human.
    Returning "allow" here would skip that callback entirely.
    """

    async def gate(input_data: Any, _tool_use_id: str | None, _context: Any) -> dict[str, Any]:
        name = str(input_data.get("tool_name", ""))
        if name in permitted:
            return {}
        log.warning("tool_blocked_by_gate", tool=name)
        return {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": f"{name} is not enabled for this assistant.",
            }
        }

    return gate


def build_hooks(permitted: frozenset[str]) -> dict[HookEvent, list[HookMatcher]]:
    """The `hooks` option for `ClaudeAgentOptions`."""
    return {"PreToolUse": [HookMatcher(matcher=None, hooks=[build_tool_gate(permitted)])]}


__all__ = ["build_hooks", "build_tool_gate"]
