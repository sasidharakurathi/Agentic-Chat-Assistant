"""Which canvas nodes a step of a run touched (task 5.9).

A run's trace lists what the agent did; this maps each step back to the
graph, so the canvas can light up the nodes a run (or one step of it) went
through. It works from what a tool call carries: its name, and for a
database or an MCP server, which one.

- `mcp__caps__kb_search`, `kb_list_sources` -> the knowledge base node;
- `sql_*`, `mongo_*` -> the database node for the call's `connection_id`;
- `http_request`, `calculator`, `datetime`, `WebSearch` -> that tool node;
- `memory` -> the memory node;
- `mcp__<server>__<tool>` -> that MCP server's node (by the server's name);
- `Agent` (a delegation) -> the subagent node for its role;
- a guardrail finding -> the guardrail node.

Something the graph has no node for (a tool since removed from the draft)
maps to nothing rather than to a guess.
"""

from __future__ import annotations

from typing import Any

from app.agent.caps import CAPS_SERVER_NAME
from app.agent.options import SUBAGENT_TOOL, WEB_SEARCH_TOOL
from app.graph.nodes import Graph

_DB_TOOLS = ("sql_query", "sql_introspect", "sql_list_schemas", "mongo_find", "mongo_aggregate")

#: The platform's own tools: the node type, the node field that must match
#: (None: any node of the type), and whether the value comes from the call's
#: input (a database's `connection_id`) or is the tool's own name (a tool
#: node's `key`).
_CAPS: dict[str, tuple[str, str | None, bool]] = {
    "kb_search": ("knowledge_base", None, False),
    "kb_list_sources": ("knowledge_base", None, False),
    **dict.fromkeys(_DB_TOOLS, ("database", "connection_id", True)),
    **dict.fromkeys(("http_request", "calculator", "datetime"), ("tool", "key", False)),
    "memory": ("memory", None, False),
}

#: Nodes every run goes through: the message passes the guardrails (their
#: rules and checks apply to every turn) on its way to the agent.
ALWAYS = ("input", "guardrail", "agent", "output")


def _ids(graph: Graph, node_type: str, **data: Any) -> list[str]:
    return [
        n.id
        for n in graph.nodes
        if n.type == node_type
        and all(str(getattr(n.data, k, None)) == str(v) for k, v in data.items())
    ]


def nodes_for_tool(
    graph: Graph, name: str, tool_input: Any, mcp_ids_by_name: dict[str, str]
) -> list[str]:
    args = tool_input if isinstance(tool_input, dict) else {}
    if name == WEB_SEARCH_TOOL:
        return _ids(graph, "tool", key="web_search")
    if name == SUBAGENT_TOOL:
        return _ids(graph, "subagent", role=args.get("subagent_type", ""))
    prefix, _, rest = name.partition("__")
    server, _, tool = rest.partition("__")
    if prefix != "mcp" or not tool:
        return []
    if server != CAPS_SERVER_NAME:
        server_id = mcp_ids_by_name.get(server)
        return _ids(graph, "mcp_server", mcp_server_id=server_id) if server_id else []
    return _caps_nodes(graph, tool, args)


def _caps_nodes(graph: Graph, tool: str, args: dict[str, Any]) -> list[str]:
    if tool not in _CAPS:
        return []
    node_type, field, from_input = _CAPS[tool]
    if field is None:
        return _ids(graph, node_type)
    return _ids(graph, node_type, **{field: args.get(field, "") if from_input else tool})


def guardrail_nodes(graph: Graph) -> list[str]:
    return _ids(graph, "guardrail")


def always_nodes(graph: Graph) -> list[str]:
    return [n.id for n in graph.nodes if n.type in ALWAYS]


__all__ = ["always_nodes", "guardrail_nodes", "nodes_for_tool"]
