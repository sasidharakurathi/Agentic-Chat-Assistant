"""Property-based tests for the graph <-> config round trip (task 1.12).

The round-trip and determinism guarantees were checked only on two hand-made
fixtures, and the "determinism" test compiled the *same object* twice, so it
could not detect order dependence. That dependence was real: with two
guardrail nodes, compile took whichever was listed first.

Hypothesis generates configs across the whole space (tools, knowledge base,
databases, MCP servers, subagents, models, limits) and checks the
properties on every one:

1. compile(project(config)) == config                (nothing lost)
2. project is a fixed point                          (stable ids/positions)
3. compile ignores node and edge order                (determinism)
"""

from __future__ import annotations

import uuid
from typing import Any

from app.graph.compile import compile_graph
from app.graph.nodes import (
    AgentNode,
    Edge,
    Graph,
    GuardrailNode,
    GuardrailNodeData,
    InputNode,
    OutputNode,
    OutputNodeData,
    RouterNode,
    RouterNodeData,
)
from app.graph.project import project_config
from app.graph.validate import validate_graph
from app.schemas.assistant_config import AssistantConfig, ModelSpec
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

_uuids = st.builds(lambda: str(uuid.uuid4()))
_models = st.sampled_from(["claude-haiku-4-5", "claude-sonnet-5", "claude-opus-5"])
_efforts = st.sampled_from(["low", "medium", "high", "xhigh", "max"])


@st.composite
def configs(draw: st.DrawFn) -> AssistantConfig:
    rag_on = draw(st.booleans())
    raw: dict[str, Any] = {
        "system_prompt": draw(st.text(max_size=40)),
        "models": {
            "main": {
                "model": draw(_models),
                "effort": draw(_efforts),
                "thinking": {"type": draw(st.sampled_from(["adaptive", "disabled"]))},
                "max_turns": draw(st.none() | st.integers(1, 200)),
                "max_budget_usd": draw(
                    st.none() | st.floats(0.01, 100, allow_nan=False, allow_infinity=False)
                ),
            }
        },
        "guardrails": {
            "rules": draw(st.lists(st.text(min_size=1, max_size=20), max_size=3)),
            "pii_redaction": draw(st.booleans()),
        },
        "tools": {
            "calculator": {"enabled": draw(st.booleans())},
            "datetime": {"enabled": draw(st.booleans())},
        },
        "rag": {
            "enabled": rag_on,
            "citations": draw(st.booleans()),
            "source_ids": draw(st.lists(_uuids, max_size=3)) if rag_on else [],
        },
        "databases": [
            {"connection_id": cid, "expose_write": draw(st.booleans())}
            for cid in draw(st.lists(_uuids, max_size=3, unique=True))
        ],
        "mcp_servers": draw(st.lists(_uuids, max_size=2, unique=True)),
        "subagents": {"retrieval": rag_on and draw(st.booleans())},
    }
    return AssistantConfig.model_validate(raw)


_settings = settings(max_examples=150, deadline=None, suppress_health_check=[HealthCheck.too_slow])


@_settings
@given(configs())
def test_nothing_is_lost_in_the_round_trip(cfg: AssistantConfig) -> None:
    graph = project_config(cfg)
    result = validate_graph(graph)
    assert result.ok, result.errors
    assert compile_graph(graph) == cfg


@_settings
@given(configs())
def test_projection_is_a_fixed_point(cfg: AssistantConfig) -> None:
    once = project_config(cfg)
    twice = project_config(compile_graph(once), existing_graph=once)
    assert once.model_dump() == twice.model_dump()


@_settings
@given(configs(), st.randoms(use_true_random=False))
def test_compile_ignores_node_and_edge_order(cfg: AssistantConfig, rnd: Any) -> None:
    """The old test compiled the same object twice; this one shuffles."""
    graph = project_config(cfg)
    nodes, edges = list(graph.nodes), list(graph.edges)
    rnd.shuffle(nodes)
    rnd.shuffle(edges)
    shuffled = Graph(schema_version=graph.schema_version, nodes=nodes, edges=edges)
    assert compile_graph(shuffled) == compile_graph(graph)


# ── the specific ambiguities that made order matter ──────────


def _base(*extra: Any, edges: list[Edge] | None = None) -> Graph:
    return Graph(
        nodes=[InputNode(id="in"), AgentNode(id="a"), OutputNode(id="out"), *extra],
        edges=[Edge(source="in", target="a"), Edge(source="a", target="out"), *(edges or [])],
    )


def test_two_guardrail_nodes_are_an_error_not_a_coin_flip() -> None:
    g = _base(
        GuardrailNode(id="g1", data=GuardrailNodeData(rules=["one"])),
        GuardrailNode(id="g2", data=GuardrailNodeData(rules=["two"])),
        edges=[Edge(source="g1", target="a"), Edge(source="g2", target="a")],
    )
    codes = [e.code for e in validate_graph(g).errors]
    assert "duplicate_node" in codes


def test_an_unwired_router_changes_nothing() -> None:
    """An opus router left unconnected used to become the router model."""
    router = RouterNode(
        id="r", data=RouterNodeData(model=ModelSpec(model="claude-opus-5", effort="max"))
    )
    baseline = compile_graph(_base())
    assert compile_graph(_base(router)).models.router == baseline.models.router
    wired = compile_graph(_base(router, edges=[Edge(source="r", target="a")]))
    assert wired.models.router.model == "claude-opus-5", "wired, it still applies"


def test_the_output_nodes_citations_switch_is_honoured() -> None:
    off = _base()
    off.nodes[2] = OutputNode(id="out", data=OutputNodeData(citations=False))
    assert compile_graph(off).rag.citations is False
    assert compile_graph(_base()).rag.citations is True


def test_a_missing_guardrail_says_the_defaults_apply() -> None:
    codes = {w.code for w in validate_graph(_base()).warnings}
    assert "defaults_in_use" in codes
