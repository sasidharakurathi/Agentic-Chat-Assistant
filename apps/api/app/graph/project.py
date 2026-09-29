"""``project_config(config, existing_graph=None) -> Graph``.

The reverse of :func:`~app.graph.compile.compile_graph`: build a canvas graph from
a config so the form panels and the canvas stay in sync. Node ids are
deterministic; when ``existing_graph`` is given, matching nodes keep their
on-canvas positions.
"""

from __future__ import annotations

from app.graph.nodes import (
    AgentNode,
    AgentNodeData,
    AnyNode,
    DatabaseNode,
    DatabaseNodeData,
    DataSourceNode,
    DataSourceNodeData,
    Edge,
    Graph,
    GuardrailNode,
    GuardrailNodeData,
    InputNode,
    KnowledgeBaseNode,
    KnowledgeBaseNodeData,
    McpServerNode,
    McpServerNodeData,
    MemoryNode,
    MemoryNodeData,
    OutputNode,
    OutputNodeData,
    Position,
    SubagentNode,
    SubagentNodeData,
    ToolNode,
    ToolNodeData,
)
from app.schemas.assistant_config import AssistantConfig, SubagentRole

_COL_INPUT = 0.0
_COL_PRE = 260.0
_COL_SRC = 380.0
_COL_CAP = 640.0
_COL_SUB = 820.0
_COL_AGENT = 1040.0
_COL_OUTPUT = 1320.0


def project_config(config: AssistantConfig, existing_graph: Graph | None = None) -> Graph:
    canonical = _project(config, existing_graph)
    if existing_graph is None:
        return canonical
    return _keep_user_wiring(config, canonical, existing_graph)


def _identity(node: AnyNode) -> tuple[str, ...]:
    """What a node *is*, independent of its id: its type, plus whatever it
    references. The canvas and the projection mint different ids for the same
    thing ("db" vs "db:{connection_id}"), so ids cannot be the match key."""
    data = node.data
    ref = next(
        (
            str(getattr(data, attr))
            for attr in ("connection_id", "data_source_id", "key", "mcp_server_id", "role")
            if getattr(data, attr, None) is not None
        ),
        None,
    )
    return (node.type,) if ref is None else (node.type, ref)


def _project(config: AssistantConfig, existing_graph: Graph | None) -> Graph:
    existing: dict[tuple[str, ...], AnyNode] = {}
    for n in existing_graph.nodes if existing_graph else []:
        existing.setdefault(_identity(n), n)

    def ident(identity: tuple[str, ...], default: str) -> str:
        """The node's id on the user's canvas if it is already there."""
        found = existing.get(identity)
        return found.id if found is not None else default

    pos = {n.id: n.position for n in existing_graph.nodes} if existing_graph else {}

    def at(node_id: str, x: float, y: float) -> Position:
        return pos.get(node_id, Position(x=x, y=y))

    i_in = ident(("input",), "input")
    i_guard = ident(("guardrail",), "guardrail")
    i_mem = ident(("memory",), "memory")
    i_agent = ident(("agent",), "agent")
    i_out = ident(("output",), "output")

    nodes: list = []
    edges: list[Edge] = []

    nodes.append(InputNode(id=i_in, position=at(i_in, _COL_INPUT, 0)))
    nodes.append(
        GuardrailNode(
            id=i_guard,
            position=at(i_guard, _COL_PRE, -120),
            data=GuardrailNodeData(**config.guardrails.model_dump()),
        )
    )
    nodes.append(
        MemoryNode(
            id=i_mem,
            position=at(i_mem, _COL_CAP, 420),
            data=MemoryNodeData(**config.memory.model_dump()),
        )
    )
    nodes.append(
        AgentNode(
            id=i_agent,
            position=at(i_agent, _COL_AGENT, 0),
            data=AgentNodeData(
                system_prompt=config.system_prompt,
                models=config.models.model_copy(deep=True),
                approval_policy=config.approval_policy.model_copy(deep=True),
            ),
        )
    )
    nodes.append(
        OutputNode(
            id=i_out,
            position=at(i_out, _COL_OUTPUT, 0),
            data=OutputNodeData(citations=config.rag.citations),
        )
    )
    edges += [
        Edge(source=i_in, target=i_guard),
        Edge(source=i_guard, target=i_agent),
        Edge(source=i_mem, target=i_agent),
        Edge(source=i_agent, target=i_out),
    ]

    y = 0.0
    if config.rag.enabled:
        i_kb = ident(("knowledge_base",), "kb")
        nodes.append(
            KnowledgeBaseNode(
                id=i_kb,
                position=at(i_kb, _COL_CAP, y),
                data=KnowledgeBaseNodeData(
                    embedder=config.rag.embedder,
                    reranker=config.rag.reranker,
                    chunking=config.rag.chunking.model_copy(deep=True),
                    contextual_retrieval=config.rag.contextual_retrieval,
                    retrieval=config.rag.retrieval.model_copy(deep=True),
                    citations=config.rag.citations,
                ),
            )
        )
        edges.append(Edge(source=i_kb, target=i_agent))
        y += 140
        for i, sid in enumerate(config.rag.source_ids):
            nid = ident(("data_source", sid), f"src:{sid}")
            nodes.append(
                DataSourceNode(
                    id=nid,
                    position=at(nid, _COL_SRC, i * 90),
                    data=DataSourceNodeData(data_source_id=sid),
                )
            )
            edges.append(Edge(source=nid, target=i_kb))

    for db in config.databases:
        nid = ident(("database", db.connection_id), f"db:{db.connection_id}")
        nodes.append(
            DatabaseNode(
                id=nid,
                position=at(nid, _COL_CAP, y),
                data=DatabaseNodeData(
                    connection_id=db.connection_id,
                    nl2sql=db.nl2sql,
                    expose_write=db.expose_write,
                ),
            )
        )
        edges.append(Edge(source=nid, target=i_agent))
        y += 120

    for key, cfg, approval in _enabled_tools(config):
        nid = ident(("tool", key), f"tool:{key}")
        nodes.append(
            ToolNode(
                id=nid,
                position=at(nid, _COL_CAP, y),
                data=ToolNodeData(key=key, config=cfg, approval=approval),
            )
        )
        edges.append(Edge(source=nid, target=i_agent))
        y += 110

    for ref in config.mcp_servers:
        nid = ident(("mcp_server", ref.id), f"mcp:{ref.id}")
        nodes.append(
            McpServerNode(
                id=nid,
                position=at(nid, _COL_CAP, y),
                data=McpServerNodeData(
                    mcp_server_id=ref.id,
                    tool_allowlist=list(ref.tools),
                    approval=ref.approval,
                    tool_approvals=dict(ref.tool_approvals),
                ),
            )
        )
        edges.append(Edge(source=nid, target=i_agent))
        y += 110

    sy = 0.0
    for role in ("retrieval", "sql", "research"):
        if getattr(config.subagents, role):
            nid = ident(("subagent", role), f"sub:{role}")
            nodes.append(
                SubagentNode(
                    id=nid,
                    position=at(nid, _COL_SUB, sy),
                    data=SubagentNodeData(role=role),
                )
            )
            edges.append(Edge(source=nid, target=i_agent))
            sy += 120

    return Graph(nodes=nodes, edges=edges)


def _keep_user_wiring(config: AssistantConfig, canonical: Graph, existing: Graph) -> Graph:
    """Prefer the edges the user drew, when they mean the same thing.

    The projection wires everything straight to the agent, but the canvas
    allows equivalent shapes (a database wired through a subagent compiles
    to the same config). Rewiring on every Panels save would change the
    user's canvas for no semantic reason. So: keep every existing edge whose
    endpoints survive, add canonical edges only for nodes that are new, and
    use the result only if it still validates and compiles to exactly this
    config — otherwise the canonical wiring is the safe answer.
    """
    # Imported here: compile/validate depend on nothing in this module, but
    # keeping projection importable on its own keeps the layering obvious.
    from app.graph.compile import compile_graph
    from app.graph.validate import validate_graph

    ids = {n.id for n in canonical.nodes}
    old_ids = {n.id for n in existing.nodes}
    kept = [e for e in existing.edges if e.source in ids and e.target in ids]
    added = [e for e in canonical.edges if e.source not in old_ids or e.target not in old_ids]
    seen: set[tuple[str, str]] = set()
    edges: list[Edge] = []
    for e in [*kept, *added]:
        if (e.source, e.target) not in seen:
            seen.add((e.source, e.target))
            edges.append(e)
    candidate = Graph(nodes=canonical.nodes, edges=edges)
    try:
        if validate_graph(candidate).ok and compile_graph(candidate) == config:
            return candidate
    except (ValueError, KeyError):
        pass
    return canonical


def _enabled_tools(config: AssistantConfig) -> list[tuple[str, dict[str, object], str]]:
    t = config.tools
    out: list[tuple[str, dict[str, object], str]] = []
    if t.web_search.enabled:
        out.append(
            (
                "web_search",
                {
                    "max_uses": t.web_search.max_uses,
                    "allowed_domains": list(t.web_search.allowed_domains),
                },
                "auto",
            )
        )
    if t.http_request.enabled:
        out.append(
            (
                "http_request",
                {"allowed_domains": list(t.http_request.allowed_domains)},
                t.http_request.approval,
            )
        )
    if t.calculator.enabled:
        out.append(("calculator", {}, "auto"))
    if t.datetime.enabled:
        out.append(("datetime", {}, "auto"))
    return out


_ = SubagentRole  # keep the import meaningful for type readers

__all__ = ["project_config"]
