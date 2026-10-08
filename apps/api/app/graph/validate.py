"""Structural validation of a :class:`~app.graph.nodes.Graph`.

Pure and I/O-free. Errors block publish; warnings do not. Ownership checks
(does this ``connection_id`` belong to the assistant?) live in the service layer,
not here.
"""

from __future__ import annotations

import re

from pydantic import Field

from app.graph import fixes
from app.graph.fixes import FixOp, GraphFix
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
    #: A suggested one-click fix (task 5.11), when there is a safe one.
    fix: GraphFix | None = None


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


def _pos(graph: Graph, node_type: str, dx: float, dy: float) -> dict[str, float]:
    """A spot relative to the first node of a type (the agent, say)."""
    anchor = next((n for n in graph.nodes if n.type == node_type), None)
    x, y = (anchor.position.x, anchor.position.y) if anchor else (0.0, 0.0)
    return {"x": x + dx, "y": y + dy}


def _first(graph: Graph, node_type: str) -> str | None:
    return next((n.id for n in graph.nodes if n.type == node_type), None)


def _add_node_fix(graph: Graph, node_type: str) -> GraphFix | None:
    """Add a missing input, output, guardrails or memory node, wired in."""
    agent = _first(graph, "agent")
    if agent is None:
        return None
    nid = fixes.free_id(graph, node_type)
    guard, inp = _first(graph, "guardrail"), _first(graph, "input")
    layout = {
        "input": (-1040.0, 0.0, [(nid, guard or agent)]),
        "output": (280.0, 0.0, [(agent, nid)]),
        "guardrail": (-780.0, -120.0, [*([(inp, nid)] if inp else []), (nid, agent)]),
        "memory": (-400.0, 420.0, [(nid, agent)]),
    }
    dx, dy, edges = layout[node_type]
    label = {"guardrail": "guardrails", "memory": "memory"}.get(node_type, node_type)
    return GraphFix(
        label=f"Add a {label} node",
        ops=[
            FixOp(
                op="add_node",
                node_id=nid,
                node_type=node_type,
                data={},
                position=_pos(graph, "agent", dx, dy),
            ),
            *(FixOp(op="add_edge", source=a, target=b) for a, b in edges),
        ],
    )


def _check_node_counts(graph: Graph, res: ValidationResult) -> None:
    """Required singletons, at-most-one types, and defaulted types."""
    nodes = graph.nodes
    for t in SINGLETON_TYPES:
        of_type = [n for n in nodes if n.type == t]
        if not of_type:
            res.errors.append(
                GraphIssue(
                    code="missing_node",
                    message=f"graph needs exactly one {t} node",
                    fix=_add_node_fix(graph, t) if t != "agent" else None,
                )
            )
        elif len(of_type) > 1:
            res.errors.append(_duplicates(t, of_type, "expected 1"))
    for t in AT_MOST_ONE_TYPES:
        of_type = [n for n in nodes if n.type == t]
        if len(of_type) > 1:
            res.errors.append(_duplicates(t, of_type, "at most one is allowed"))
    for t in _DEFAULTED_TYPES:
        if not any(n.type == t for n in nodes):
            res.warnings.append(
                GraphIssue(
                    code="defaults_in_use",
                    message=f"no {t} node: the default {t} settings apply",
                    fix=_add_node_fix(graph, t),
                )
            )


def _duplicates(node_type: str, of_type: list[AnyNode], rule: str) -> GraphIssue:
    """Two where one is allowed: the extras (all but the first) can go."""
    extras = of_type[1:]
    return GraphIssue(
        code="duplicate_node",
        message=f"graph has {len(of_type)} {node_type} nodes; {rule}",
        node_id=extras[-1].id,
        fix=GraphFix(
            label=f"Remove the extra {node_type} node" + ("s" if len(extras) > 1 else ""),
            ops=[FixOp(op="remove_node", node_id=n.id) for n in extras],
        ),
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
                    fix=fixes.remove_edge(e.source, e.target),
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
                    fix=fixes.remove_edge(e.source, e.target),
                )
            )
            continue
        adj[e.source].append(e.target)

    # ── singletons ───────────────────────────────────────────
    agent_ids = [n.id for n in nodes if n.type == "agent"]
    _check_node_counts(graph, res)

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
                    fix=fixes.remove_node(extra.id),
                )
            )

    # ── duplicate capability refs ────────────────────────────
    _dup_ref_check(graph, res)
    _http_tool_warnings(graph, res)

    # Everything past here is best-effort reachability guidance (warnings only);
    # skip it if the graph is already structurally broken.
    if agent_ids and not res.errors:
        _reachability_warnings(graph, agent_ids[0], adj, res)

    _ = by_id  # reserved for future per-node data checks
    return res


def _http_tool_warnings(graph: Graph, res: ValidationResult) -> None:
    """The HTTP request tool reaches only its allowed sites (Phase 7a.9):
    with none listed it refuses everything, and a site anyone can write to
    still lets data out. Said here, before anyone chats."""
    from app.agent.caps_http import open_domains

    for n in graph.nodes:
        if n.type != "tool" or getattr(n.data, "key", None) != "http_request":
            continue
        raw = (getattr(n.data, "config", None) or {}).get("allowed_domains") or []
        domains = [str(d) for d in raw] if isinstance(raw, list) else []
        if not [d for d in domains if d.strip()]:
            res.warnings.append(
                GraphIssue(
                    code="http_no_allowed_sites",
                    message=(
                        "The HTTP request tool has no allowed sites, so every request it "
                        "makes will be refused. List the sites it may reach."
                    ),
                    node_id=n.id,
                )
            )
            continue
        for d in open_domains(domains):
            res.warnings.append(
                GraphIssue(
                    code="http_open_site",
                    message=(
                        f"Anyone can publish on {d} (a paste, a gist, a form), so allowing "
                        "it lets the assistant send data anywhere. Allow a narrower site."
                    ),
                    node_id=n.id,
                )
            )


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

    def live(kind: str, key: str | None = None) -> bool:
        """A capability of this kind this subagent can use: wired to the
        agent (it inherits those), or to this subagent itself (task 5.10).
        One wired only to another subagent is that one's alone."""
        return any(
            type_of[k] == kind
            and (key is None or getattr(by_id[k].data, "key", None) == key)
            and (agent_id in adj[k] or node.id in adj[k])
            for k in type_of
        )

    by_id = graph.by_id()

    def give(kind: str, what: str, key: str | None = None) -> GraphFix:
        """Wire an existing capability of this kind into the subagent, or,
        with none on the canvas, offer to remove the subagent."""
        have = next(
            (
                k
                for k in type_of
                if type_of[k] == kind
                and (key is None or getattr(by_id[k].data, "key", None) == key)
            ),
            None,
        )
        if have is not None:
            return fixes.wire(f"Give it the {what}", (have, node.id))
        return fixes.remove_node(node.id, "Remove the subagent")

    if role == "retrieval":
        if live("knowledge_base"):
            return None
        return GraphIssue(
            code="subagent_without_knowledge_base",
            message=(
                "the retrieval subagent has nothing to search: wire a knowledge base "
                "to it or to the agent, or it is skipped"
            ),
            node_id=node.id,
            fix=give("knowledge_base", "knowledge base"),
        )
    if role == "sql":
        if live("database"):
            return None
        return GraphIssue(
            code="subagent_without_database",
            message=(
                "the sql subagent has no database to query: wire a database to it or "
                "to the agent, or it is skipped"
            ),
            node_id=node.id,
            fix=give("database", "database"),
        )
    if live("tool", "web_search"):
        return None
    return GraphIssue(
        code="subagent_without_web_search",
        message=(
            "the research subagent needs web search: wire the Web search tool to it or to "
            "the agent, or it is skipped"
        ),
        node_id=node.id,
        fix=give("tool", "web search", "web_search"),
    )


#: Pipeline nodes the compiler ignores unless they are on the way to the
#: agent (task 5.11: they were ignored without a word).
_PIPELINE_ORPHANS: dict[str, str] = {
    "guardrail": (
        "the guardrails node isn't on the way to the agent, so its rules and checks don't apply"
    ),
    "memory": "the memory node isn't wired to the agent, so the default memory settings apply",
    "router": "the router isn't on the way to the agent, so messages aren't routed",
    "subagent": "this subagent isn't wired to the agent, so it can't be delegated to",
}


def _feeds_agent(
    graph: Graph, node_type: str, agent_id: str, adj: dict[str, list[str]]
) -> str | None:
    """A node of this type that is on the way to the agent, if any."""
    return next(
        (n.id for n in graph.nodes if n.type == node_type and _reaches(n.id, {agent_id}, adj)),
        None,
    )


def _pipeline_fix(graph: Graph, n: AnyNode, agent_id: str, adj: dict[str, list[str]]) -> GraphFix:
    """Put an unwired guardrails/memory/router/subagent node back in the
    pipeline, the way the projection wires them."""
    inp = _first(graph, "input")
    if n.type == "guardrail":
        onward = _feeds_agent(graph, "router", agent_id, adj) or agent_id
        feed = [(inp, n.id)] if inp and n.id not in adj.get(inp, []) else []
        return fixes.wire("Put it on the way to the agent", *feed, (n.id, onward))
    if n.type == "router":
        before = _feeds_agent(graph, "guardrail", agent_id, adj) or inp
        feed = [(before, n.id)] if before else []
        return fixes.wire("Put it on the way to the agent", *feed, (n.id, agent_id))
    return fixes.wire("Wire it to the agent", (n.id, agent_id))


def _reachability_warnings(
    graph: Graph, agent_id: str, adj: dict[str, list[str]], res: ValidationResult
) -> None:
    type_of = {n.id: n.type for n in graph.nodes}
    kbs = [n.id for n in graph.nodes if n.type == "knowledge_base"]

    for n in graph.nodes:
        wired = _reaches(n.id, {agent_id}, adj)
        if n.type in _PIPELINE_ORPHANS and not wired:
            res.warnings.append(
                GraphIssue(
                    code=f"orphan_{n.type}",
                    message=_PIPELINE_ORPHANS[n.type],
                    node_id=n.id,
                    fix=_pipeline_fix(graph, n, agent_id, adj),
                )
            )
            continue
        if n.type == "subagent":
            issue = _subagent_issue(n, graph, type_of, agent_id, adj)
            if issue is not None:
                res.warnings.append(issue)
        if n.type in _CAPABILITY_TYPES and not wired:
            res.warnings.append(
                GraphIssue(
                    code="orphan_capability",
                    message=f"{n.type} node {n.id!r} is not wired to the agent",
                    node_id=n.id,
                    fix=fixes.wire("Wire it to the agent", (n.id, agent_id)),
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
                    fix=(
                        fixes.wire("Connect it to the knowledge base", (n.id, kbs[0]))
                        if len(kbs) == 1
                        else None
                    ),
                )
            )

    input_ids = [n.id for n in graph.nodes if n.type == "input"]
    output_ids = [n.id for n in graph.nodes if n.type == "output"]
    if input_ids and not _reaches(input_ids[0], {agent_id}, adj):
        onward = (
            _feeds_agent(graph, "guardrail", agent_id, adj)
            or _feeds_agent(graph, "router", agent_id, adj)
            or agent_id
        )
        res.warnings.append(
            GraphIssue(
                code="input_not_wired",
                message="input node has no path to the agent",
                node_id=input_ids[0],
                fix=fixes.wire("Connect it to the agent", (input_ids[0], onward)),
            )
        )
    if output_ids and agent_id not in graph.incomers(output_ids[0]):
        res.warnings.append(
            GraphIssue(
                code="output_not_wired",
                message="agent is not connected to the output node",
                node_id=output_ids[0],
                fix=fixes.wire("Connect the agent to it", (agent_id, output_ids[0])),
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
                        fix=fixes.remove_node(n.id, "Remove this duplicate"),
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
                        fix=fixes.remove_node(n.id, "Remove this duplicate"),
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
                        fix=fixes.remove_node(n.id),
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
                        fix=fixes.patch(
                            n.id,
                            {
                                "tool_allowlist": [
                                    t for t in n.data.tool_allowlist if _TOOL_NAME.fullmatch(t)
                                ],
                                "tool_approvals": {
                                    t: m
                                    for t, m in n.data.tool_approvals.items()
                                    if _TOOL_NAME.fullmatch(t)
                                },
                            },
                            "Drop the invalid names",
                        ),
                    )
                )
            if n.data.mcp_server_id in seen_mcp:
                res.errors.append(
                    GraphIssue(
                        code="duplicate_ref",
                        message=f"two mcp_server nodes for {n.data.mcp_server_id}",
                        node_id=n.id,
                        fix=fixes.remove_node(n.id, "Remove this duplicate"),
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
                        fix=fixes.remove_node(n.id, "Remove this duplicate"),
                    )
                )
            seen_sub.add(n.data.role)


__all__ = ["GraphIssue", "ValidationResult", "validate_graph"]
