from __future__ import annotations

from app.graph.nodes import (
    AgentNode,
    DatabaseNode,
    DatabaseNodeData,
    Edge,
    Graph,
    InputNode,
    OutputNode,
    SubagentNode,
    SubagentNodeData,
    ToolNode,
    ToolNodeData,
)
from app.graph.validate import validate_graph
from tests.graph_helpers import minimal_graph, rich_graph


def test_minimal_and_rich_graphs_validate() -> None:
    assert validate_graph(minimal_graph()).ok
    r = validate_graph(rich_graph())
    assert r.ok, r.errors
    assert r.warnings == []


def test_missing_agent_is_an_error() -> None:
    g = Graph(nodes=[InputNode(id="in"), OutputNode(id="out")], edges=[])
    res = validate_graph(g)
    assert not res.ok
    assert any(e.code == "missing_node" for e in res.errors)


def test_illegal_edge_rejected() -> None:
    g = minimal_graph()
    g.edges.append(Edge(source="out", target="in"))  # output -> input is not allowed
    res = validate_graph(g)
    assert any(e.code == "illegal_edge" for e in res.errors)


def test_cycle_detected() -> None:
    g = Graph(
        nodes=[
            InputNode(id="in"),
            AgentNode(id="a"),
            OutputNode(id="out"),
            ToolNode(id="t", data=ToolNodeData(key="calculator")),
            SubagentNode(id="s", data=SubagentNodeData(role="sql")),
        ],
        edges=[
            Edge(source="in", target="a"),
            Edge(source="a", target="out"),
            Edge(source="t", target="s"),
            Edge(source="s", target="a"),
            Edge(source="a", target="s"),  # cycle: a -> s -> a
        ],
    )
    assert any(e.code in {"cycle", "illegal_edge"} for e in validate_graph(g).errors)


def test_bare_subagent_is_warning_in_v1() -> None:
    g = minimal_graph()
    g.nodes.append(SubagentNode(id="s", data=SubagentNodeData(role="sql")))
    g.edges.append(Edge(source="s", target="a"))
    res = validate_graph(g)
    assert res.ok
    assert any(w.code == "bare_subagent" for w in res.warnings)


def test_orphan_capability_is_warning_not_error() -> None:
    g = minimal_graph()
    g.nodes.append(DatabaseNode(id="db", data=DatabaseNodeData(connection_id="c")))
    # no edge from db -> anything
    res = validate_graph(g)
    assert res.ok
    assert any(w.code == "orphan_capability" for w in res.warnings)


def test_duplicate_capability_ref_is_error() -> None:
    g = minimal_graph()
    g.nodes.append(DatabaseNode(id="db1", data=DatabaseNodeData(connection_id="dup")))
    g.nodes.append(DatabaseNode(id="db2", data=DatabaseNodeData(connection_id="dup")))
    g.edges.append(Edge(source="db1", target="a"))
    g.edges.append(Edge(source="db2", target="a"))
    assert any(e.code == "duplicate_ref" for e in validate_graph(g).errors)


# ── knowledge base singleton (task 2.13) ─────────────────────


def test_a_second_knowledge_base_is_an_error_not_a_silent_no_op() -> None:
    """compile_graph takes kb_nodes[0], so a second knowledge base's settings
    would be dead config the user believes is live. Unreachable before 2.13
    made these nodes creatable from the canvas."""
    from app.graph.nodes import KnowledgeBaseNode

    g = minimal_graph()
    g.nodes.extend([KnowledgeBaseNode(id="kb1"), KnowledgeBaseNode(id="kb2")])
    g.edges.extend([Edge(source="kb1", target="a"), Edge(source="kb2", target="a")])

    res = validate_graph(g)
    assert not res.ok
    codes = [e.code for e in res.errors]
    assert codes == ["duplicate_knowledge_base"]
    # The *extra* node is flagged, not the one that actually takes effect.
    assert [e.node_id for e in res.errors] == ["kb2"]


def test_one_knowledge_base_is_still_fine() -> None:
    from app.graph.nodes import KnowledgeBaseNode

    g = minimal_graph()
    g.nodes.append(KnowledgeBaseNode(id="kb1"))
    g.edges.append(Edge(source="kb1", target="a"))
    assert validate_graph(g).ok


def test_data_source_must_go_through_a_knowledge_base() -> None:
    """The edge allow-list the canvas now serves to the frontend: a data
    source wired straight at the agent is illegal."""
    from app.graph.nodes import DataSourceNode, DataSourceNodeData

    g = minimal_graph()
    g.nodes.append(DataSourceNode(id="ds1", data=DataSourceNodeData(data_source_id="abc")))
    g.edges.append(Edge(source="ds1", target="a"))
    res = validate_graph(g)
    assert not res.ok
    assert [e.code for e in res.errors] == ["illegal_edge"]
