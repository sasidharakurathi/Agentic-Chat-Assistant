"""The graph <-> config round-trip: the Phase 1 spike (ADR 0004).

Guarantee: projecting a config to a graph and compiling it back loses no
semantics, and doing it twice is a fixed point.
"""

from __future__ import annotations

from app.graph.compile import compile_graph
from app.graph.nodes import Position
from app.graph.project import project_config
from app.graph.validate import validate_graph
from app.schemas.assistant_config import AssistantConfig, default_config
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
