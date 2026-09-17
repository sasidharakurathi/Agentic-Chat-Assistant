"""Tool-permission policy — the ``can_use_tool`` callback.

Deny-by-default (ADR 0003): anything not explicitly allowed is refused. The
full human-in-the-loop flow (a pending row + an SSE ``approval_required`` event +
awaiting a decision) lands in Phase 3; in Phase 1 a ``require`` policy simply
denies with a clear message, and the only enabled tools are read-only ``caps``.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any, Literal

from claude_agent_sdk import PermissionResultAllow, PermissionResultDeny, ToolPermissionContext

from app.agent.caps import CAPS_SERVER_NAME
from app.logging import get_logger
from app.schemas.assistant_config import ApprovalMode, ApprovalPolicy

log = get_logger(__name__)

Risk = Literal["low", "medium", "high"]

CanUseTool = Callable[
    [str, dict[str, Any], ToolPermissionContext],
    Awaitable[PermissionResultAllow | PermissionResultDeny],
]

_READ_ONLY_CAPS = {f"mcp__{CAPS_SERVER_NAME}__calculator", f"mcp__{CAPS_SERVER_NAME}__datetime"}


def classify(
    tool_name: str, tool_input: dict[str, Any], policy: ApprovalPolicy
) -> tuple[ApprovalMode, Risk]:
    if tool_name in _READ_ONLY_CAPS or tool_name == "WebSearch":
        return "auto", "low"
    if tool_name == f"mcp__{CAPS_SERVER_NAME}__http_request":
        method = str(tool_input.get("method", "GET")).upper()
        if method == "GET":
            return "auto", "low"
        return policy.http_non_get, "medium"
    if tool_name == f"mcp__{CAPS_SERVER_NAME}__sql_query":
        return policy.db_write, "high"  # refined in Phase 3 by parsing the statement
    if tool_name.startswith("mcp__"):  # a user MCP server tool
        return policy.mcp_default, "medium"
    return "deny", "high"  # Bash / Write / Edit / anything unrecognised


def build_can_use_tool(policy: ApprovalPolicy) -> CanUseTool:
    async def can_use_tool(
        tool_name: str, tool_input: dict[str, Any], _ctx: ToolPermissionContext
    ) -> PermissionResultAllow | PermissionResultDeny:
        mode, risk = classify(tool_name, tool_input, policy)
        if mode == "auto" and risk == "low":
            return PermissionResultAllow()
        if mode == "deny":
            log.info("tool_denied", tool=tool_name, risk=risk)
            return PermissionResultDeny(message=f"{tool_name} is not permitted for this assistant.")
        # mode == "require" (or auto+non-low): needs a human — not wired until Phase 3.
        log.info("tool_needs_approval_unavailable", tool=tool_name, risk=risk)
        return PermissionResultDeny(
            message=(
                f"{tool_name} needs human approval, which isn't available yet. "
                "Try a read-only alternative."
            ),
        )

    return can_use_tool


__all__ = ["CanUseTool", "Risk", "build_can_use_tool", "classify"]
