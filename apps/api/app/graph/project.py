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
    RouterNode,
    RouterNodeData,
    SubagentNode,
    SubagentNodeData,
    ToolNode,
    ToolNodeData,
)
from app.schemas.assistant_config import AssistantConfig, Scoped, SubagentRole

_COL_INPUT = 0.0
_COL_PRE = 260.0
_COL_SRC = 380.0
_COL_CAP = 640.0
_COL_SUB = 820.0
_COL_AGENT = 1040.0
_COL_OUTPUT = 1320.0

#: Column per node type: the same as the canvas's Tidy up
#: (apps/web/components/canvas/graph-sync.ts), so a new graph looks tidied.
_COLUMN: dict[str, float] = {
    "input": _COL_INPUT,
    "guardrail": _COL_PRE,
    "data_source": _COL_SRC,
    "knowledge_base": _COL_CAP,
    "database": _COL_CAP,
    "tool": _COL_CAP,
    "mcp_server": _COL_CAP,
    "memory": _COL_CAP,
    "subagent": _COL_SUB,
    "router": _COL_SUB,
    "agent": _COL_AGENT,
    "output": _COL_OUTPUT,
}
#: Rows are 96px apart (four 24px grid cells): a 52px station plus room.
_ROW_GAP = 96.0
_MAIN_LINE = ("input", "guardrail", "router", "agent", "output")
_BRANCH_ORDER = (
    "knowledge_base",
    "memory",
    "database",
    "tool",
    "mcp_server",
    "data_source",
    "subagent",
)


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


class _Projection:
    """Builds the canonical graph for one config. Node ids and positions are
    the user's where the same node is already on their canvas."""

    def __init__(self, config: AssistantConfig, existing_graph: Graph | None) -> None:
        self.config = config
        self.existing: dict[tuple[str, ...], AnyNode] = {}
        for n in existing_graph.nodes if existing_graph else []:
            self.existing.setdefault(_identity(n), n)
        self.pos = {n.id: n.position for n in existing_graph.nodes} if existing_graph else {}
        #: Nodes that were not on the canvas yet: :func:`_layout` places them.
        self.fresh: set[str] = set()
        self.nodes: list[AnyNode] = []
        self.edges: list[Edge] = []
        self.agent = self.ident(("agent",), "agent")
        # Subagent nodes' ids first: capabilities scoped to a subagent (task
        # 5.10) are wired to its node.
        roles: tuple[SubagentRole, ...] = ("retrieval", "sql", "research")
        self.sub_ids: dict[SubagentRole, str] = {
            role: self.ident(("subagent", role), f"sub:{role}")
            for role in roles
            if getattr(config.subagents, role)
        }

    def ident(self, identity: tuple[str, ...], default: str) -> str:
        """The node's id on the user's canvas if it is already there."""
        found = self.existing.get(identity)
        return found.id if found is not None else default

    def at(self, node_id: str) -> Position:
        """The node's position on the user's canvas; a new node is placed by
        :func:`_layout` once the whole graph is known."""
        found = self.pos.get(node_id)
        if found is None:
            self.fresh.add(node_id)
            return Position()
        return found

    def wire(self, node_id: str, cap: Scoped) -> None:
        """A capability's edges: to the agent, and/or to its subagents."""
        if cap.agent:
            self.edges.append(Edge(source=node_id, target=self.agent))
        self.edges.extend(
            Edge(source=node_id, target=self.sub_ids[r]) for r in cap.subagents if r in self.sub_ids
        )

    def pipeline(self) -> None:
        """Input, guardrails, [router,] agent, output, and memory."""
        config, at = self.config, self.at
        i_in = self.ident(("input",), "input")
        i_guard = self.ident(("guardrail",), "guardrail")
        i_mem = self.ident(("memory",), "memory")
        i_out = self.ident(("output",), "output")
        self.nodes += [
            InputNode(id=i_in, position=at(i_in)),
            GuardrailNode(
                id=i_guard,
                position=at(i_guard),
                data=GuardrailNodeData(**config.guardrails.model_dump()),
            ),
            MemoryNode(
                id=i_mem,
                position=at(i_mem),
                data=MemoryNodeData(**config.memory.model_dump()),
            ),
            AgentNode(
                id=self.agent,
                position=at(self.agent),
                data=AgentNodeData(
                    system_prompt=config.system_prompt,
                    models=config.models.model_copy(deep=True),
                    approval_policy=config.approval_policy.model_copy(deep=True),
                ),
            ),
            OutputNode(
                id=i_out,
                position=at(i_out),
                data=OutputNodeData(citations=config.rag.citations),
            ),
        ]
        self.edges += [
            Edge(source=i_in, target=i_guard),
            Edge(source=i_mem, target=self.agent),
            Edge(source=self.agent, target=i_out),
        ]
        if not config.router.enabled:
            self.edges.append(Edge(source=i_guard, target=self.agent))
            return
        # Between the guardrails and the agent: every message passes it.
        i_router = self.ident(("router",), "router")
        self.nodes.append(
            RouterNode(
                id=i_router,
                position=at(i_router),
                data=RouterNodeData(model=config.models.router.model_copy(deep=True)),
            )
        )
        self.edges += [
            Edge(source=i_guard, target=i_router),
            Edge(source=i_router, target=self.agent),
        ]

    def capabilities(self) -> None:
        config, at, ident = self.config, self.at, self.ident
        if config.rag.enabled:
            i_kb = ident(("knowledge_base",), "kb")
            self.nodes.append(
                KnowledgeBaseNode(
                    id=i_kb,
                    position=at(i_kb),
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
            self.wire(i_kb, config.rag)
            for sid in config.rag.source_ids:
                nid = ident(("data_source", sid), f"src:{sid}")
                self.nodes.append(
                    DataSourceNode(
                        id=nid,
                        position=at(nid),
                        data=DataSourceNodeData(data_source_id=sid),
                    )
                )
                self.edges.append(Edge(source=nid, target=i_kb))

        for db in config.databases:
            nid = ident(("database", db.connection_id), f"db:{db.connection_id}")
            self.nodes.append(
                DatabaseNode(
                    id=nid,
                    position=at(nid),
                    data=DatabaseNodeData(
                        connection_id=db.connection_id,
                        nl2sql=db.nl2sql,
                        expose_write=db.expose_write,
                    ),
                )
            )
            self.wire(nid, db)

        for key, cfg, approval in _enabled_tools(config):
            nid = ident(("tool", key), f"tool:{key}")
            self.nodes.append(
                ToolNode(
                    id=nid,
                    position=at(nid),
                    data=ToolNodeData(key=key, config=cfg, approval=approval),
                )
            )
            self.wire(nid, getattr(config.tools, key))

        for ref in config.mcp_servers:
            nid = ident(("mcp_server", ref.id), f"mcp:{ref.id}")
            self.nodes.append(
                McpServerNode(
                    id=nid,
                    position=at(nid),
                    data=McpServerNodeData(
                        mcp_server_id=ref.id,
                        tool_allowlist=list(ref.tools),
                        approval=ref.approval,
                        tool_approvals=dict(ref.tool_approvals),
                    ),
                )
            )
            self.wire(nid, ref)

    def subagents(self) -> None:
        for role, nid in self.sub_ids.items():
            # The role's own model settings live on its node (task 5.1): the
            # model without its turn limit, and the limit as the node's own.
            own = self.config.subagents.models.get(role)
            self.nodes.append(
                SubagentNode(
                    id=nid,
                    position=self.at(nid),
                    data=SubagentNodeData(
                        role=role,
                        model=own.model_copy(update={"max_turns": None}) if own else None,
                        max_turns=own.max_turns if own else None,
                    ),
                )
            )
            self.edges.append(Edge(source=nid, target=self.agent))


def _project(config: AssistantConfig, existing_graph: Graph | None) -> Graph:
    p = _Projection(config, existing_graph)
    p.pipeline()
    p.capabilities()
    p.subagents()
    placed = _layout(p.nodes)
    for n in p.nodes:
        if n.id in p.fresh:
            n.position = placed[n.id]
    if existing_graph is not None:
        _settle(p.nodes, p.fresh)
    return Graph(nodes=p.nodes, edges=p.edges)


#: Room a station needs when checking whether a spot is taken: wider than
#: most plates, one row tall plus space for lines between rows. The same as
#: the canvas's ``makeRoom`` (apps/web/components/canvas/graph-sync.ts).
_CLEAR_X = 200.0
_CLEAR_Y = 76.0


def _overlaps(a: Position, b: Position) -> bool:
    return abs(a.x - b.x) < _CLEAR_X and abs(a.y - b.y) < _CLEAR_Y


def _settle(nodes: list[AnyNode], fresh: set[str]) -> None:
    """New nodes land where a tidy canvas would put them, but the user's
    canvas may not be tidy: older layouts had capabilities on the main row,
    and the user may have moved things. So, on an existing canvas:

    - a new main-line station (the router, say) goes on the agent's row, and
      any capability already in its way moves to the nearest free row in its
      own column;
    - a new capability that would sit on another station moves to the
      nearest free row in its column, never onto the main row.

    Only positions change, and only where two stations would stack."""
    agent = next((n for n in nodes if n.type == "agent"), None)
    main_y = agent.position.y if agent is not None else 0.0

    def free(n: AnyNode, spot: Position) -> bool:
        return not any(o is not n and _overlaps(o.position, spot) for o in nodes)

    def move_off_main(n: AnyNode) -> None:
        # Rows above and below the main row, nearest to where it is first.
        rows = sorted(
            (main_y + k * sign * _ROW_GAP for k in range(1, len(nodes) + 2) for sign in (-1, 1)),
            key=lambda y: (abs(y - n.position.y), y),
        )
        for y in rows:
            spot = Position(x=n.position.x, y=y)
            if free(n, spot):
                n.position = spot
                return

    for n in nodes:
        if n.id not in fresh:
            continue
        if n.type in _MAIN_LINE:
            if agent is not None and agent.id not in fresh:
                n.position = Position(x=n.position.x, y=main_y)
            for o in nodes:
                if o is not n and o.type not in _MAIN_LINE and _overlaps(o.position, n.position):
                    move_off_main(o)
        elif not free(n, n.position) or abs(n.position.y - main_y) < _CLEAR_Y:
            move_off_main(n)


def _layout(nodes: list[AnyNode]) -> dict[str, Position]:
    """Where each node goes on a tidy canvas: the canvas's Tidy up, in
    Python. Every node in its type's column; the main line (input,
    guardrails, router, agent, output) on one row, y=0, so it runs straight;
    a capability never on that row. Branch columns fill rows -1, +1, -2, +2
    and so on, except that data sources stay on their knowledge base's side
    and subagents start outside the capability rows."""
    columns: dict[float, list[AnyNode]] = {}
    for n in nodes:
        columns.setdefault(_COLUMN.get(n.type, _COL_CAP), []).append(n)

    def rank(t: str, order: tuple[str, ...]) -> int:
        return order.index(t) if t in order else len(order)

    row: dict[str, int] = {}
    kb = next((n for n in nodes if n.type == "knowledge_base"), None)

    def place(x: float) -> None:
        ns = sorted(
            columns.get(x, []),
            key=lambda n: (rank(n.type, _MAIN_LINE), rank(n.type, _BRANCH_ORDER), n.id),
        )
        mains = [n for n in ns if n.type in _MAIN_LINE]
        if mains:
            row[mains[0].id] = 0
        branches = mains[1:] + [n for n in ns if n.type not in _MAIN_LINE]
        start, side = 1, 0
        if x == _COL_SUB:
            start = max([0, *(abs(row.get(n.id, 0)) for n in columns.get(_COL_CAP, []))]) + 1
        if x == _COL_SRC and kb is not None:
            kb_row = row.get(kb.id, 0)
            side = (kb_row > 0) - (kb_row < 0)
        for i, n in enumerate(branches):
            row[n.id] = side * (start + i) if side else (1 if i % 2 else -1) * (start + i // 2)

    for x in [_COL_CAP, *(x for x in columns if x != _COL_CAP)]:
        if x in columns:
            place(x)
    return {
        n.id: Position(x=_COLUMN.get(n.type, _COL_CAP), y=row.get(n.id, 0) * _ROW_GAP)
        for n in nodes
    }


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


__all__ = ["project_config"]
