from __future__ import annotations

import pytest
from app.graph.compile import GraphCompileError, compile_graph
from app.graph.nodes import Edge, Graph, InputNode, OutputNode
from tests.graph_helpers import minimal_graph, rich_graph


def test_compile_minimal_gives_default_shaped_config() -> None:
    c = compile_graph(minimal_graph())
    assert c.rag.enabled is False
    assert c.databases == []
    assert c.subagents.retrieval is False and c.subagents.sql is False
    assert c.models.main.model == "sonnet"


def test_compile_rich_graph_projects_wired_nodes() -> None:
    c = compile_graph(rich_graph())
    assert c.rag.enabled is True
    assert [d.connection_id for d in c.databases] == ["conn-1", "conn-2"]
    assert c.databases[1].expose_write is True
    assert c.tools.calculator.enabled is True
    assert c.tools.datetime.enabled is False
    assert c.subagents.sql is True
    assert c.subagents.retrieval is False


# Determinism is tested for real in test_graph_properties.py: this test
# compiled the *same object* twice, so it could never detect order dependence.


def test_compile_rejects_invalid_graph() -> None:
    g = Graph(
        nodes=[InputNode(id="in"), OutputNode(id="out")], edges=[Edge(source="in", target="out")]
    )
    with pytest.raises(GraphCompileError):
        compile_graph(g)


def test_unwired_capability_is_ignored_by_compiler() -> None:
    from app.graph.nodes import DatabaseNode, DatabaseNodeData

    g = minimal_graph()
    g.nodes.append(DatabaseNode(id="db", data=DatabaseNodeData(connection_id="c")))
    # deliberately not wired -> validate warns, compile ignores it
    c = compile_graph(g)
    assert c.databases == []
