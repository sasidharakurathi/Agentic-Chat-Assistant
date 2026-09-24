"""Builders for graph fixtures used across the graph tests."""

from __future__ import annotations

from app.graph.nodes import (
    AgentNode,
    DatabaseNode,
    DatabaseNodeData,
    Edge,
    Graph,
    GuardrailNode,
    InputNode,
    KnowledgeBaseNode,
    MemoryNode,
    OutputNode,
    SubagentNode,
    SubagentNodeData,
    ToolNode,
    ToolNodeData,
)


def minimal_graph() -> Graph:
    """input -> guardrail -> agent -> output (+ nothing else)."""
    return Graph(
        nodes=[
            InputNode(id="in"),
            GuardrailNode(id="g"),
            AgentNode(id="a"),
            OutputNode(id="out"),
        ],
        edges=[
            Edge(source="in", target="g"),
            Edge(source="g", target="a"),
            Edge(source="a", target="out"),
        ],
    )


def rich_graph() -> Graph:
    """A graph exercising rag, a db behind a subagent, a tool, and a direct db."""
    return Graph(
        nodes=[
            InputNode(id="in"),
            GuardrailNode(id="g"),
            AgentNode(id="a"),
            OutputNode(id="out"),
            MemoryNode(id="mem"),
            KnowledgeBaseNode(id="kb"),
            DatabaseNode(id="db1", data=DatabaseNodeData(connection_id="conn-1")),
            DatabaseNode(
                id="db2", data=DatabaseNodeData(connection_id="conn-2", expose_write=True)
            ),
            ToolNode(id="t1", data=ToolNodeData(key="calculator")),
            SubagentNode(id="s1", data=SubagentNodeData(role="sql")),
        ],
        edges=[
            Edge(source="in", target="g"),
            Edge(source="g", target="a"),
            Edge(source="a", target="out"),
            Edge(source="mem", target="a"),
            Edge(source="kb", target="a"),
            Edge(source="db1", target="a"),
            Edge(source="db2", target="s1"),
            Edge(source="s1", target="a"),
            Edge(source="t1", target="a"),
        ],
    )
