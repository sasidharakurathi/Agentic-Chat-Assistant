"""The graph <-> config round-trip: the Phase 1 spike (ADR 0004).

Guarantee: projecting a config to a graph and compiling it back loses no
semantics, and doing it twice is a fixed point.
"""

from __future__ import annotations

from app.graph.compile import compile_graph
from app.graph.nodes import (
    AgentNode,
    DatabaseNode,
    DatabaseNodeData,
    DataSourceNode,
    DataSourceNodeData,
    Edge,
    Graph,
    InputNode,
    KnowledgeBaseNode,
    OutputNode,
    Position,
)
from app.graph.project import project_config
from app.graph.validate import validate_graph
from app.schemas.assistant_config import AssistantConfig, DatabaseRef, default_config
from tests.graph_helpers import rich_graph


def _canonical_rich_config() -> AssistantConfig:
    return AssistantConfig.model_validate(
        {
            "system_prompt": "You answer questions about orders.",
            "models": {"main": {"model": "claude-opus-5", "effort": "xhigh"}},
            "guardrails": {
                "rules": ["cite sources", "never expose secrets"],
                "pii_redaction": False,
            },
            "rag": {
                "enabled": True,
                "contextual_retrieval": False,
                "retrieval": {"rerank_top_n": 12, "top_k_dense": 60, "top_k_sparse": 60},
                "source_ids": ["src-b", "src-a"],
            },
            "databases": [
                {"connection_id": "conn-b", "expose_write": True},
                {"connection_id": "conn-a"},
            ],
            "tools": {
                "web_search": {
                    "enabled": True,
                    "max_uses": 3,
                    "allowed_domains": ["docs.example.com"],
                },
                "http_request": {"enabled": True, "approval": "require"},
                "calculator": {"enabled": True},
            },
            "mcp_servers": ["11111111-1111-1111-1111-111111111111"],
            "subagents": {"retrieval": True, "sql": True, "research": False},
            "memory": {"summarize_after_tokens": 90000, "auto_title": False},
        }
    )


def test_default_config_round_trips_exactly() -> None:
    cfg = default_config()
    assert compile_graph(project_config(cfg)) == cfg


def test_rich_config_round_trips_exactly() -> None:
    cfg = _canonical_rich_config()
    graph = project_config(cfg)
    assert validate_graph(graph).ok, validate_graph(graph).errors
    assert compile_graph(graph) == cfg


def test_projection_is_a_fixed_point() -> None:
    cfg = compile_graph(rich_graph())
    g1 = project_config(cfg)
    c1 = compile_graph(g1)
    g2 = project_config(c1)
    c2 = compile_graph(g2)
    assert c1 == c2
    assert g1.model_dump() == g2.model_dump()  # ids + positions stable


def test_projection_preserves_existing_positions() -> None:
    cfg = _canonical_rich_config()
    first = project_config(cfg)
    # move the agent node
    for n in first.nodes:
        if n.id == "agent":
            n.position = Position(x=999, y=42)
    second = project_config(cfg, existing_graph=first)
    agent = next(n for n in second.nodes if n.id == "agent")
    assert (agent.position.x, agent.position.y) == (999, 42)
    # and the config still compiles back unchanged
    assert compile_graph(second) == cfg


# ── the canvas's own node ids survive a Panels save ─────────────
#
# The canvas mints short ids ("db", "ds", "kb-2") while the projection used to
# re-derive its own ("db:{connection_id}", "src:{source_id}") and look saved
# positions up *by id* — so the first save through PUT /draft-config found no
# match, snapped every such node to a default spot and renamed it. Nodes are
# now matched by what they reference, and keep their id and position.


def _canvas_built_graph() -> Graph:
    return Graph(
        nodes=[
            InputNode(id="input", position=Position(x=1, y=1)),
            AgentNode(id="agent", position=Position(x=2, y=2)),
            OutputNode(id="output", position=Position(x=3, y=3)),
            KnowledgeBaseNode(id="kb-1", position=Position(x=11, y=11)),
            DataSourceNode(
                id="ds",
                position=Position(x=22, y=22),
                data=DataSourceNodeData(data_source_id="src-a"),
            ),
            DatabaseNode(
                id="db",
                position=Position(x=33, y=33),
                data=DatabaseNodeData(connection_id="conn-a"),
            ),
        ],
        edges=[
            Edge(source="input", target="agent"),
            Edge(source="agent", target="output"),
            Edge(source="ds", target="kb-1"),
            Edge(source="kb-1", target="agent"),
            Edge(source="db", target="agent"),
        ],
    )


def test_canvas_minted_ids_and_positions_survive_projection() -> None:
    canvas = _canvas_built_graph()
    projected = project_config(compile_graph(canvas), existing_graph=canvas)
    by_id = {n.id: n for n in projected.nodes}
    for nid, xy in {"kb-1": (11, 11), "ds": (22, 22), "db": (33, 33)}.items():
        assert nid in by_id, f"{nid} was renamed"
        assert (by_id[nid].position.x, by_id[nid].position.y) == xy, f"{nid} moved"
    assert not any(n.id.startswith(("db:", "src:")) for n in projected.nodes)
    assert compile_graph(projected) == compile_graph(canvas)


def test_a_config_edit_moves_nothing_else() -> None:
    """What the Panels tab actually does: change one setting in the config."""
    canvas = _canvas_built_graph()
    cfg = compile_graph(canvas).model_copy(update={"system_prompt": "Be brief."})
    projected = project_config(cfg, existing_graph=canvas)
    before = {n.id: (n.position.x, n.position.y) for n in canvas.nodes}
    after = {n.id: (n.position.x, n.position.y) for n in projected.nodes}
    assert {k: after[k] for k in before if k in after} == before
    assert compile_graph(projected).system_prompt == "Be brief."


def test_the_users_wiring_is_kept_when_it_means_the_same_thing() -> None:
    """A database wired through a subagent compiles the same as one wired
    straight to the agent — so a Panels save must not rewire it."""
    g = rich_graph()
    projected = project_config(compile_graph(g), existing_graph=g)
    edges = {(e.source, e.target) for e in projected.edges}
    assert ("db2", "s1") in edges
    assert compile_graph(projected) == compile_graph(g)


def test_new_config_entries_still_get_canonical_nodes() -> None:
    canvas = _canvas_built_graph()
    cfg = compile_graph(canvas)
    cfg = cfg.model_copy(
        update={"databases": [*cfg.databases, DatabaseRef(connection_id="conn-new")]}
    )
    projected = project_config(cfg, existing_graph=canvas)
    new = [n for n in projected.nodes if getattr(n.data, "connection_id", None) == "conn-new"]
    assert len(new) == 1
    assert compile_graph(projected) == AssistantConfig.model_validate(cfg.model_dump())
