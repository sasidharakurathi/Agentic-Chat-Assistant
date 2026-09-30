"""Who may use which capability, at run time (task 5.10).

On the canvas a capability wired to the agent is the main agent's, and one
wired to a subagent is that subagent's (`Scoped.agent` / `.subagents` in
the config). A subagent can also use what the main agent has, within its
role's own tools.

All tools live on the same in-process servers for the main agent and its
subagents, so the scope is enforced per call, by the PreToolUse gate: the
SDK tells a hook which subagent is calling (`agent_id` / `agent_type` in the
hook input) and says nothing when the main agent is. A database is told
apart by the call's `connection_id`, an MCP server by the tool's server.

What each subagent is *offered* follows the same rule
(`extra_tools`): its role's tools, plus the tools of whatever is wired to it.
"""

from __future__ import annotations

from collections.abc import Collection
from typing import Any

from app.agent.caps import CAPS_SERVER_NAME
from app.schemas.assistant_config import AssistantConfig, Scoped, SubagentRole

WEB_SEARCH = "WebSearch"
_KB = ("kb_search", "kb_list_sources")
_DB = ("sql_list_schemas", "sql_introspect", "sql_query", "mongo_find", "mongo_aggregate")
_TOOLS = ("http_request", "calculator", "datetime")


def _is_db_tool(tool: str) -> bool:
    """A database tool serves every database; which one is in its input."""
    return tool in {f"mcp__{CAPS_SERVER_NAME}__{n}" for n in _DB}


def _platform(
    config: AssistantConfig, name: str, tool_input: dict[str, Any]
) -> tuple[Scoped, str] | None:
    """The capability behind one of the platform's own tools."""
    if name in _KB:
        return config.rag, "knowledge base"
    if name in _DB:
        conn = str(tool_input.get("connection_id", ""))
        db = next((d for d in config.databases if d.connection_id == conn), None)
        return (db, "database") if db else None
    if name in _TOOLS:
        return getattr(config.tools, name), name.replace("_", " ")
    return None


def _what(
    config: AssistantConfig, tool: str, tool_input: dict[str, Any], mcp_ids: dict[str, str]
) -> tuple[Scoped, str] | None:
    """The capability a call uses, and what to call it in a message."""
    if tool == WEB_SEARCH:
        return config.tools.web_search, "web search"
    prefix, _, rest = tool.partition("__")
    server, _, name = rest.partition("__")
    if prefix != "mcp" or not name:
        return None
    if server == CAPS_SERVER_NAME:
        return _platform(config, name, tool_input)
    ref = next((m for m in config.mcp_servers if m.id == mcp_ids.get(tool)), None)
    return (ref, f"{server} MCP server") if ref else None


def refusal(
    config: AssistantConfig,
    caller: str | None,
    tool: str,
    tool_input: dict[str, Any],
    mcp_ids: dict[str, str],
) -> str | None:
    """Why `caller` (None: the main agent; else a subagent's role) may not
    make this call, or None when it may."""
    found = _what(config, tool, tool_input, mcp_ids)
    if found is None:
        return None
    cap, label = found
    if cap.usable_by(caller):
        return None
    owners = " and ".join(f"the {r} subagent" for r in cap.subagents)
    if caller is None:
        return (
            f"This {label} is only available to {owners}: delegate to it rather than "
            f"calling {tool} yourself."
        )
    return f"This {label} isn't wired to the {caller} subagent, so it can't use it."


def can_use(
    config: AssistantConfig, caller: str | None, tool: str, mcp_ids: dict[str, str]
) -> bool:
    """Whether `caller` has anything behind this tool at all: a database
    tool as long as one of the databases is usable by it."""
    if _is_db_tool(tool):
        return any(d.usable_by(caller) for d in config.databases)
    return refusal(config, caller, tool, {}, mcp_ids) is None


def main_agent_tools(
    config: AssistantConfig, tools: Collection[str], mcp_ids: dict[str, str]
) -> list[str]:
    """The tools the main agent may call at all."""
    return [t for t in tools if can_use(config, None, t, mcp_ids)]


def extra_tools(
    config: AssistantConfig,
    role: SubagentRole,
    available: Collection[str],
    mcp_ids: dict[str, str],
) -> list[str]:
    """The tools of what is wired to this subagent itself, beyond its role's
    own: a calculator wired to the sql subagent, say."""
    out: list[str] = []
    for tool in available:
        if _is_db_tool(tool):
            wired = any(role in d.subagents for d in config.databases)
        else:
            found = _what(config, tool, {}, mcp_ids)
            wired = found is not None and role in found[0].subagents
        if wired:
            out.append(tool)
    return out


__all__ = ["can_use", "extra_tools", "main_agent_tools", "refusal"]
