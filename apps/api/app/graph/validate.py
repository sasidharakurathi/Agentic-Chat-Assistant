"""Structural validation of a :class:`~app.graph.nodes.Graph`.

Pure and I/O-free. Errors block publish; warnings do not. Ownership checks
(does this ``connection_id`` belong to the assistant?) live in the service layer,
not here.
"""

from __future__ import annotations

import re

from pydantic import Field

from app.graph.nodes import AnyNode, Graph, NodeType
from app.schemas.common import ApiModel

# Which (source_type -> target_type) edges are meaningful.
ALLOWED_EDGES: set[tuple[NodeType, NodeType]] = {
    ("input", "guardrail"),
    ("input", "router"),
    ("input", "agent"),
    ("guardrail", "router"),
    ("guardrail", "agent"),
    ("router", "agent"),
    ("data_source", "knowledge_base"),
    ("knowledge_base", "agent"),
    ("knowledge_base", "subagent"),
    ("database", "agent"),
    ("database", "subagent"),
    ("tool", "agent"),
    ("tool", "subagent"),
    ("mcp_server", "agent"),
    ("mcp_server", "subagent"),
    ("subagent", "agent"),
    ("memory", "agent"),
    ("agent", "output"),
}

_CAPABILITY_TYPES: set[NodeType] = {"knowledge_base", "database", "tool", "mcp_server"}
SINGLETON_TYPES: tuple[NodeType, ...] = ("input", "agent", "output")
#: Optional, but never more than one: the compiler reads a single node of
#: each. With two, it took whichever happened to be listed first, so the
#: compiled config depended on node order (task 1.12's determinism gap).
AT_MOST_ONE_TYPES: tuple[NodeType, ...] = ("guardrail", "memory", "router")
#: Absent, these fall back to schema defaults; say so rather than silently.
_DEFAULTED_TYPES: tuple[NodeType, ...] = ("guardrail", "memory")


class GraphIssue(ApiModel):
    code: str
    message: str
    node_id: str | None = None
    edge: tuple[str, str] | None = None


class ValidationResult(ApiModel):
    errors: list[GraphIssue] = Field(default_factory=list)
    warnings: list[GraphIssue] = Field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors


def _has_cycle(node_ids: set[str], adj: dict[str, list[str]]) -> bool:
    WHITE, GRAY, BLACK = 0, 1, 2
    color = dict.fromkeys(node_ids, WHITE)

    def visit(u: str) -> bool:
        color[u] = GRAY
        for v in adj.get(u, []):
            if color.get(v, BLACK) == GRAY:
                return True
            if color.get(v, BLACK) == WHITE and visit(v):
                return True
        color[u] = BLACK
        return False

    return any(color[n] == WHITE and visit(n) for n in node_ids)


def _reaches(start: str, targets: set[str], adj: dict[str, list[str]]) -> bool:
    seen: set[str] = set()
    stack = [start]
    while stack:
        u = stack.pop()
        if u in targets:
            return True
        if u in seen:
            continue
        seen.add(u)
        stack.extend(adj.get(u, []))
    return False


def _check_node_counts(nodes: list[AnyNode], res: ValidationResult) -> None:
    """Required singletons, at-most-one types, and defaulted types."""
    for t in SINGLETON_TYPES:
        count = sum(1 for n in nodes if n.type == t)
        if count == 0:
            res.errors.append(
                GraphIssue(code="missing_node", message=f"graph needs exactly one {t} node")
            )
        elif count > 1:
            res.errors.append(
                GraphIssue(
                    code="duplicate_node", message=f"graph has {count} {t} nodes; expected 1"
                )
            )
    for t in AT_MOST_ONE_TYPES:
        count = sum(1 for n in nodes if n.type == t)
        if count > 1:
            res.errors.append(
                GraphIssue(
                    code="duplicate_node",
                    message=f"graph has {count} {t} nodes; at most one is allowed",
                )
            )
    for t in _DEFAULTED_TYPES:
        if not any(n.type == t for n in nodes):
            res.warnings.append(
                GraphIssue(
                    code="defaults_in_use",
                    message=f"no {t} node: the default {t} settings apply",
                )
            )


def validate_graph(graph: Graph) -> ValidationResult:
    res = ValidationResult()
    nodes = graph.nodes
    ids = [n.id for n in nodes]
    id_set = set(ids)
    by_id = graph.by_id()
    type_of = {n.id: n.type for n in nodes}

    # ── unique ids ───────────────────────────────────────────
    if len(ids) != len(id_set):
        dupes = sorted({i for i in ids if ids.count(i) > 1})
        res.errors.append(
            GraphIssue(code="duplicate_node_id", message=f"duplicate node ids: {dupes}")
        )

    # ── edge endpoints exist + edge type legal ───────────────
    adj: dict[str, list[str]] = {i: [] for i in id_set}
    for e in graph.edges:
        if e.source not in id_set or e.target not in id_set:
            res.errors.append(
                GraphIssue(
                    code="dangling_edge",
                    message=f"edge {e.source} -> {e.target} references a missing node",
                    edge=(e.source, e.target),
                )
            )
            continue
        pair = (type_of[e.source], type_of[e.target])
        if pair not in ALLOWED_EDGES:
            res.errors.append(
                GraphIssue(
                    code="illegal_edge",
                    message=f"{pair[0]} cannot connect to {pair[1]}",
                    edge=(e.source, e.target),
                )
            )
            continue
        adj[e.source].append(e.target)

    # ── singletons ───────────────────────────────────────────
    agent_ids = [n.id for n in nodes if n.type == "agent"]
    _check_node_counts(nodes, res)

    # ── cycles ───────────────────────────────────────────────
    if _has_cycle(id_set, adj):
        res.errors.append(GraphIssue(code="cycle", message="graph contains a cycle"))

    # ── a second knowledge base would be silently ignored ────
    # `compile_graph` takes kb_nodes[0]; any further knowledge_base node wired
    # to the agent contributes nothing, so its settings would be dead config
    # the user believes is live. Before task 2.13 this was unreachable (no UI
    # could create one); now that the canvas can, say so out loud.
    kb_wired = [n for n in nodes if n.type == "knowledge_base"]
    if len(kb_wired) > 1:
        for extra in kb_wired[1:]:
            res.errors.append(
                GraphIssue(
                    code="duplicate_knowledge_base",
                    message=(
                        "only one knowledge base is supported; "
                        "this node's settings would be ignored"
                    ),
                    node_id=extra.id,
                )
            )

    # ── duplicate capability refs ────────────────────────────
    _dup_ref_check(graph, res)

    # Everything past here is best-effort reachability guidance (warnings only);
    # skip it if the graph is already structurally broken.
    if agent_ids and not res.errors:
        _reachability_warnings(graph, agent_ids[0], adj, res)

    _ = by_id  # reserved for future per-node data checks
    return res


def _subagent_issue(
    node: AnyNode,
    graph: Graph,
    type_of: dict[str, NodeType],
    agent_id: str,
    adj: dict[str, list[str]],
) -> GraphIssue | None:
    """What a subagent node will actually do, when that is nothing.

    In v1 a subagent is a built-in pattern with its own toolset: retrieval
    always gets the knowledge-base tools (`agent/subagents.py`). The old
    warning ("no capability wired into it") fired for every retrieval
    subagent, including correctly configured ones, and never mentioned the
    two cases that really leave it inert.
    """
    role = node.data.role  # type: ignore[union-attr]
    if role == "retrieval":
        kb_live = any(
            type_of[k] == "knowledge_base" and _reaches(k, {agent_id}, adj) for k in type_of
        )
        if kb_live:
            return None
        return GraphIssue(
            code="subagent_without_knowledge_base",
            message=(
                "the retrieval subagent has nothing to search: wire a knowledge base "
                "to the agent, or it is skipped"
            ),
            node_id=node.id,
        )
    return GraphIssue(
        code="subagent_not_available",
        message=f"the {role} subagent arrives in Phase 5; until then it has no effect",
        node_id=node.id,
    )


def _reachability_warnings(
    graph: Graph, agent_id: str, adj: dict[str, list[str]], res: ValidationResult
) -> None:
    type_of = {n.id: n.type for n in graph.nodes}

    for n in graph.nodes:
        if n.type == "subagent":
            issue = _subagent_issue(n, graph, type_of, agent_id, adj)
            if issue is not None:
                res.warnings.append(issue)
        if n.type in _CAPABILITY_TYPES and not _reaches(n.id, {agent_id}, adj):
            res.warnings.append(
                GraphIssue(
                    code="orphan_capability",
                    message=f"{n.type} node {n.id!r} is not wired to the agent",
                    node_id=n.id,
                )
            )
        if n.type == "data_source" and not any(
            type_of[t] == "knowledge_base" for t in graph.outgoers(n.id)
        ):
            res.warnings.append(
                GraphIssue(
                    code="orphan_data_source",
                    message=f"data source {n.id!r} is not connected to a knowledge base",
                    node_id=n.id,
                )
            )

    input_ids = [n.id for n in graph.nodes if n.type == "input"]
    output_ids = [n.id for n in graph.nodes if n.type == "output"]
    if input_ids and not _reaches(input_ids[0], {agent_id}, adj):
        res.warnings.append(
            GraphIssue(
                code="input_not_wired",
                message="input node has no path to the agent",
                node_id=input_ids[0],
            )
        )
    if output_ids and agent_id not in graph.incomers(output_ids[0]):
        res.warnings.append(
            GraphIssue(
                code="output_not_wired",
                message="agent is not connected to the output node",
            )
        )


#: The shapes `AssistantConfig.mcp_servers` accepts.
_UUID_SHAPE = re.compile(r"^[0-9a-fA-F-]{8,64}$")
_TOOL_NAME = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


def _dup_ref_check(graph: Graph, res: ValidationResult) -> None:
    seen_conn: set[str] = set()
    seen_tool: set[str] = set()
    seen_mcp: set[str] = set()
    seen_sub: set[str] = set()
    for n in graph.nodes:
        if n.type == "database":
            cid = n.data.connection_id
            if cid in seen_conn:
                res.errors.append(
                    GraphIssue(
                        code="duplicate_ref",
                        message=f"two database nodes use connection {cid}",
                        node_id=n.id,
                    )
                )
            seen_conn.add(cid)
        elif n.type == "tool":
            if n.data.key in seen_tool:
                res.errors.append(
                    GraphIssue(
                        code="duplicate_ref",
                        message=f"two tool nodes for {n.data.key!r}",
                        node_id=n.id,
                    )
                )
            seen_tool.add(n.data.key)
        elif n.type == "mcp_server":
            if not _UUID_SHAPE.fullmatch(n.data.mcp_server_id):
                # Caught here, not only by the reference check: the compiled
                # config requires a UUID, so a malformed one would otherwise
                # fail compilation and the draft save with it.
                res.errors.append(
                    GraphIssue(
                        code="unknown_mcp_server",
                        message="This MCP server reference is not a valid id. "
                        "Pick one from the MCP servers tab, or remove the node.",
                        node_id=n.id,
                    )
                )
            named = [*n.data.tool_allowlist, *n.data.tool_approvals]
            bad = [t for t in named if not _TOOL_NAME.fullmatch(t)]
            if bad:
                res.errors.append(
                    GraphIssue(
                        code="invalid_mcp_tool",
                        message=f"Not a valid MCP tool name: {', '.join(map(repr, bad[:5]))}. "
                        "Choose tools from the server's discovered list.",
                        node_id=n.id,
                    )
                )
            if n.data.mcp_server_id in seen_mcp:
                res.errors.append(
                    GraphIssue(
                        code="duplicate_ref",
                        message=f"two mcp_server nodes for {n.data.mcp_server_id}",
                        node_id=n.id,
                    )
                )
            seen_mcp.add(n.data.mcp_server_id)
        elif n.type == "subagent":
            if n.data.role in seen_sub:
                res.errors.append(
                    GraphIssue(
                        code="duplicate_ref",
                        message=f"two subagent nodes with role {n.data.role!r}",
                        node_id=n.id,
                    )
                )
            seen_sub.add(n.data.role)


__all__ = ["GraphIssue", "ValidationResult", "validate_graph"]
