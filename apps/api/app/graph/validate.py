"""Structural validation of a :class:`~app.graph.nodes.Graph`.

Pure and I/O-free. Errors block publish; warnings do not. Ownership checks
(does this ``connection_id`` belong to the assistant?) live in the service layer,
not here.
"""

from __future__ import annotations

from pydantic import BaseModel

from app.graph.nodes import Graph, NodeType

# Which (source_type -> target_type) edges are meaningful.
_ALLOWED_EDGES: set[tuple[NodeType, NodeType]] = {
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
_SINGLETON_TYPES: tuple[NodeType, ...] = ("input", "agent", "output")


class GraphIssue(BaseModel):
    code: str
    message: str
    node_id: str | None = None
    edge: tuple[str, str] | None = None


class ValidationResult(BaseModel):
    errors: list[GraphIssue] = []
    warnings: list[GraphIssue] = []

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
        if pair not in _ALLOWED_EDGES:
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
    for t in _SINGLETON_TYPES:
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

    # ── cycles ───────────────────────────────────────────────
    if _has_cycle(id_set, adj):
        res.errors.append(GraphIssue(code="cycle", message="graph contains a cycle"))

    # ── duplicate capability refs ────────────────────────────
    _dup_ref_check(graph, res)

    # Everything past here is best-effort reachability guidance (warnings only);
    # skip it if the graph is already structurally broken.
    if agent_ids and not res.errors:
        _reachability_warnings(graph, agent_ids[0], adj, res)

    _ = by_id  # reserved for future per-node data checks
    return res


def _reachability_warnings(
    graph: Graph, agent_id: str, adj: dict[str, list[str]], res: ValidationResult
) -> None:
    type_of = {n.id: n.type for n in graph.nodes}

    for n in graph.nodes:
        # In v1 a subagent is a built-in pattern (retrieval / sql / research) that
        # gets a standard toolset from the runtime, so a bare one is only a hint;
        # task 5.10 (user-composed subagents) will likely promote it to an error.
        if n.type == "subagent" and not (
            {type_of[s] for s in graph.incomers(n.id)} & _CAPABILITY_TYPES
        ):
            res.warnings.append(
                GraphIssue(
                    code="bare_subagent",
                    message=(
                        f"subagent {n.id!r} has no capability wired into it "
                        "(uses the default toolset)"
                    ),
                    node_id=n.id,
                )
            )
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
