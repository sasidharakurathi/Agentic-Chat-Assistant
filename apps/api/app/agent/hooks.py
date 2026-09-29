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
- **PreToolUse: web search limits.** The SDK has no option for
  `tools.web_search.max_uses` or `allowed_domains`, so the gate enforces them:
  it counts searches in the turn and refuses past the limit, and it rewrites
  each search's input so its `allowed_domains` stay inside the configured
  list.
- **Stop: finalize the turn.** `chat.run_message` finalizes in a shielded
  block on every exit path, including a client disconnect, where a Stop hook
  would never run at all.

Tools from MCP servers users register (task 4.6) are platform capabilities
too (`app/agent/caps_mcp.py`), so their results take the same post-tool
step; no separate PostToolUse hook is needed.
"""

from __future__ import annotations

from typing import Any

from claude_agent_sdk import HookMatcher
from claude_agent_sdk.types import HookEvent

from app.logging import get_logger
from app.schemas.assistant_config import WebSearchTool
from app.security.ssrf import host_allowed, normalize_domain

log = get_logger(__name__)


#: The SDK built-in for web search (duplicated from options to avoid a cycle).
WEB_SEARCH = "WebSearch"


def constrain_web_search(tool_input: dict[str, Any], allowed: list[str]) -> dict[str, Any] | None:
    """The search input with its domains held inside `allowed`, or None when
    no allowlist is configured (nothing to change).

    The model may narrow the list further, never widen it. Domains it asks
    for that fall outside the list are dropped; if none are left, the whole
    configured list is used. `blocked_domains` is removed because the search
    API refuses a request that sets both.
    """
    configured = [normalize_domain(d) for d in allowed if normalize_domain(d)]
    if not configured:
        return None
    requested = [normalize_domain(str(d)) for d in (tool_input.get("allowed_domains") or [])]
    narrowed = [d for d in requested if d and host_allowed(d, configured)]
    out = {k: v for k, v in tool_input.items() if k != "blocked_domains"}
    out["allowed_domains"] = narrowed or configured
    return out


def build_tool_gate(permitted: frozenset[str], web_search: WebSearchTool | None = None) -> Any:
    """A `PreToolUse` hook that denies any tool this assistant has not enabled.

    `can_use_tool` alone cannot be the final gate: the CLI only consults it for
    calls that would otherwise *prompt*, and some tools never prompt. A
    PreToolUse hook runs for every call regardless of permission rules, so
    this is where deny-by-default (ADR 0003) is actually enforced.

    For a permitted tool it returns no decision at all, so the normal flow
    continues and `can_use_tool` still classifies risk and waits for a human.
    Returning "allow" here would skip that callback entirely.
    """

    searches = 0

    async def gate(input_data: Any, _tool_use_id: str | None, _context: Any) -> dict[str, Any]:
        nonlocal searches
        name = str(input_data.get("tool_name", ""))
        if name not in permitted:
            log.warning("tool_blocked_by_gate", tool=name)
            return _deny(f"{name} is not enabled for this assistant.")
        if name == WEB_SEARCH and web_search is not None:
            # One gate per turn (options are built per turn), subagents
            # included, so this counts every search the turn makes.
            searches += 1
            if searches > web_search.max_uses:
                log.info("web_search_limit", max_uses=web_search.max_uses)
                return _deny(
                    f"This assistant allows {web_search.max_uses} web searches per turn, "
                    "and they have been used. Answer from what you already found."
                )
            tool_input = input_data.get("tool_input") or {}
            constrained = constrain_web_search(dict(tool_input), web_search.allowed_domains)
            if constrained is not None:
                # `updatedInput` only applies alongside a decision. "allow"
                # is safe for WebSearch: `can_use_tool` treats it as a
                # no-action tool and would allow it anyway.
                return {
                    "hookSpecificOutput": {
                        "hookEventName": "PreToolUse",
                        "permissionDecision": "allow",
                        "updatedInput": constrained,
                    }
                }
        return {}

    return gate


def _deny(reason: str) -> dict[str, Any]:
    return {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": reason,
        }
    }


def build_hooks(
    permitted: frozenset[str], web_search: WebSearchTool | None = None
) -> dict[HookEvent, list[HookMatcher]]:
    """The `hooks` option for `ClaudeAgentOptions`."""
    return {
        "PreToolUse": [HookMatcher(matcher=None, hooks=[build_tool_gate(permitted, web_search)])]
    }


__all__ = ["build_hooks", "build_tool_gate", "constrain_web_search"]
