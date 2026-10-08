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
    # Its database reaches the agent through the sql subagent, which is all
    # that subagent needs (task 5.1): no warnings.
    assert [w.code for w in r.warnings] == []


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


def test_an_sql_subagent_without_a_database_is_flagged() -> None:
    g = minimal_graph()
    g.nodes.append(SubagentNode(id="s", data=SubagentNodeData(role="sql")))
    g.edges.append(Edge(source="s", target="a"))
    res = validate_graph(g)
    assert res.ok
    assert _subagent_warnings(res) == ["subagent_without_database"]


def test_a_research_subagent_needs_web_search() -> None:
    g = minimal_graph()
    g.nodes.append(SubagentNode(id="s", data=SubagentNodeData(role="research")))
    g.edges.append(Edge(source="s", target="a"))
    assert _subagent_warnings(validate_graph(g)) == ["subagent_without_web_search"]
    # Another tool doesn't count; web search does, wired either way.
    g.nodes.append(ToolNode(id="calc", data=ToolNodeData(key="calculator")))
    g.edges.append(Edge(source="calc", target="a"))
    assert _subagent_warnings(validate_graph(g)) == ["subagent_without_web_search"]
    g.nodes.append(ToolNode(id="web", data=ToolNodeData(key="web_search")))
    g.edges.append(Edge(source="web", target="s"))
    assert _subagent_warnings(validate_graph(g)) == []


def test_a_retrieval_subagent_without_a_knowledge_base_is_flagged() -> None:
    g = minimal_graph()
    g.nodes.append(SubagentNode(id="s", data=SubagentNodeData(role="retrieval")))
    g.edges.append(Edge(source="s", target="a"))
    assert _subagent_warnings(validate_graph(g)) == ["subagent_without_knowledge_base"]


def test_a_retrieval_subagent_with_a_knowledge_base_is_fine() -> None:
    """It gets the knowledge-base tools itself; nothing needs wiring into it.
    This used to warn "no capability wired into it" on a correct setup."""
    from app.graph.nodes import KnowledgeBaseNode, KnowledgeBaseNodeData

    g = minimal_graph()
    g.nodes.append(KnowledgeBaseNode(id="kb", data=KnowledgeBaseNodeData()))
    g.nodes.append(SubagentNode(id="s", data=SubagentNodeData(role="retrieval")))
    g.edges += [Edge(source="kb", target="a"), Edge(source="s", target="a")]
    assert _subagent_warnings(validate_graph(g)) == []


def _subagent_warnings(res: object) -> list[str]:
    return [w.code for w in res.warnings if w.code.startswith("subagent")]  # type: ignore[attr-defined]


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


# ── the HTTP tool reaches only its allowed sites (Phase 7a.9) ─


def _http_warnings(g: Graph) -> list[str]:
    return [w.code for w in validate_graph(g).warnings if w.code.startswith("http_")]


def _with_http(domains: list[str] | None) -> Graph:
    g = minimal_graph()
    config: dict[str, object] = {} if domains is None else {"allowed_domains": domains}
    g.nodes.append(ToolNode(id="http", data=ToolNodeData(key="http_request", config=config)))
    g.edges.append(Edge(source="http", target="a"))
    return g


def test_an_http_tool_with_no_allowed_sites_is_flagged() -> None:
    assert _http_warnings(_with_http(None)) == ["http_no_allowed_sites"]
    assert _http_warnings(_with_http([])) == ["http_no_allowed_sites"]
    assert _http_warnings(_with_http(["  "])) == ["http_no_allowed_sites"]
    (issue,) = [w for w in validate_graph(_with_http([])).warnings if w.code.startswith("http_")]
    assert issue.node_id == "http" and "refused" in issue.message


def test_an_allowed_site_anyone_can_publish_on_is_flagged() -> None:
    assert _http_warnings(_with_http(["helpdesk.example.com"])) == []
    flagged = _http_warnings(_with_http(["helpdesk.example.com", "pastebin.com", "google.com"]))
    assert flagged == ["http_open_site", "http_open_site"]


def test_a_graph_with_errors_still_gets_the_http_warning() -> None:
    """Not reachability guidance: it is said even while the canvas is broken."""
    g = _with_http([])
    g.nodes = [n for n in g.nodes if n.type != "agent"]
    g.edges = [e for e in g.edges if "a" not in (e.source, e.target)]
    res = validate_graph(g)
    assert res.errors and "http_no_allowed_sites" in [w.code for w in res.warnings]
