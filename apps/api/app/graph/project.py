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
    pos = {n.id: n.position for n in existing_graph.nodes} if existing_graph else {}

    def at(node_id: str, x: float, y: float) -> Position:
        return pos.get(node_id, Position(x=x, y=y))

    nodes: list = []
    edges: list[Edge] = []

    nodes.append(InputNode(id="input", position=at("input", _COL_INPUT, 0)))
    nodes.append(
        GuardrailNode(
            id="guardrail",
            position=at("guardrail", _COL_PRE, -120),
            data=GuardrailNodeData(**config.guardrails.model_dump()),
        )
    )
    nodes.append(
        MemoryNode(
            id="memory",
            position=at("memory", _COL_CAP, 420),
            data=MemoryNodeData(**config.memory.model_dump()),
        )
    )
    nodes.append(
        AgentNode(
            id="agent",
            position=at("agent", _COL_AGENT, 0),
            data=AgentNodeData(
                system_prompt=config.system_prompt,
                models=config.models.model_copy(deep=True),
                approval_policy=config.approval_policy.model_copy(deep=True),
            ),
        )
    )
    nodes.append(
        OutputNode(
            id="output",
            position=at("output", _COL_OUTPUT, 0),
            data=OutputNodeData(citations=config.rag.citations),
        )
    )
    edges += [
        Edge(source="input", target="guardrail"),
        Edge(source="guardrail", target="agent"),
        Edge(source="memory", target="agent"),
        Edge(source="agent", target="output"),
    ]

    y = 0.0
    if config.rag.enabled:
        nodes.append(
            KnowledgeBaseNode(
                id="kb",
                position=at("kb", _COL_CAP, y),
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
        edges.append(Edge(source="kb", target="agent"))
        y += 140
        for i, sid in enumerate(config.rag.source_ids):
            nid = f"src:{sid}"
            nodes.append(
                DataSourceNode(
                    id=nid,
                    position=at(nid, _COL_SRC, i * 90),
                    data=DataSourceNodeData(data_source_id=sid),
                )
            )
            edges.append(Edge(source=nid, target="kb"))

    for db in config.databases:
        nid = f"db:{db.connection_id}"
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
        edges.append(Edge(source=nid, target="agent"))
        y += 120

    for key, cfg, approval in _enabled_tools(config):
        nid = f"tool:{key}"
        nodes.append(
            ToolNode(
                id=nid,
                position=at(nid, _COL_CAP, y),
                data=ToolNodeData(key=key, config=cfg, approval=approval),
            )
        )
        edges.append(Edge(source=nid, target="agent"))
        y += 110

    for mid in config.mcp_servers:
        nid = f"mcp:{mid}"
        nodes.append(
            McpServerNode(
                id=nid,
                position=at(nid, _COL_CAP, y),
                data=McpServerNodeData(mcp_server_id=mid),
            )
        )
        edges.append(Edge(source=nid, target="agent"))
        y += 110

    sy = 0.0
    for role in ("retrieval", "sql", "research"):
        if getattr(config.subagents, role):
            nid = f"sub:{role}"
            nodes.append(
                SubagentNode(
                    id=nid,
                    position=at(nid, _COL_SUB, sy),
                    data=SubagentNodeData(role=role),
                )
            )
            edges.append(Edge(source=nid, target="agent"))
            sy += 120

    return Graph(nodes=nodes, edges=edges)


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
