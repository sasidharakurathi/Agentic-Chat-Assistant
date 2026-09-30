"""One-click fixes for graph issues, and the warnings they fix (task 5.11).

The property that matters: a suggested fix, applied, clears the issue it
was offered for and introduces no new error. Every broken graph below is
checked that way, fix by fix.
"""

from __future__ import annotations

from typing import Any

import pytest
from app.graph.compile import compile_graph
from app.graph.fixes import apply_fix
from app.graph.nodes import Edge, Graph
from app.graph.project import project_config
from app.graph.validate import GraphIssue, validate_graph
from app.schemas.assistant_config import AssistantConfig
from httpx import AsyncClient

DB = "11111111-1111-1111-1111-111111111111"
SRC = "22222222-2222-2222-2222-222222222222"
MCP = "33333333-3333-3333-3333-333333333333"


def _base(**over: Any) -> Graph:
    cfg = {
        "guardrails": {"rules": ["Be kind."]},
        "rag": {"enabled": True, "source_ids": [SRC]},
        "databases": [{"connection_id": DB}],
        "tools": {"calculator": {"enabled": True}},
        "subagents": {"sql": True},
        "router": {"enabled": True},
        **over,
    }
    return project_config(AssistantConfig.model_validate(cfg))


def _edit(
    graph: Graph,
    *,
    drop: list[tuple[str, str]] = (),
    add: list[tuple[str, str]] = (),
    remove: list[str] = (),
    extra: list[dict[str, Any]] = (),
) -> Graph:  # type: ignore[assignment]
    raw = graph.model_dump()
    raw["nodes"] = [n for n in raw["nodes"] if n["id"] not in remove] + list(extra)
    raw["edges"] = [
        e
        for e in raw["edges"]
        if (e["source"], e["target"]) not in drop and not ({e["source"], e["target"]} & set(remove))
    ] + [{"source": s, "target": t} for s, t in add]
    return Graph.model_validate(raw)


def _key(i: GraphIssue) -> tuple[Any, ...]:
    return (i.code, i.node_id, i.edge)


def _check_fixes(graph: Graph) -> list[GraphIssue]:
    """Apply every suggested fix, one at a time: each must clear its issue
    and add no error. Returns the issues, for further assertions."""
    before = validate_graph(graph)
    issues = [*before.errors, *before.warnings]
    for issue in issues:
        if issue.fix is None:
            continue
        after = validate_graph(apply_fix(graph, issue.fix))
        remaining = {_key(i) for i in [*after.errors, *after.warnings]}
        assert _key(issue) not in remaining, (issue.code, issue.fix.label)
        new_errors = {e.code for e in after.errors} - {e.code for e in before.errors}
        assert not new_errors, (issue.code, new_errors)
    return issues


def _codes(graph: Graph) -> dict[str, GraphIssue]:
    res = validate_graph(graph)
    return {i.code: i for i in [*res.errors, *res.warnings]}


def test_a_healthy_graph_has_nothing_to_fix() -> None:
    assert _check_fixes(_base()) == []


# ── what the compiler used to ignore without a word ──────────


def test_an_unwired_guardrails_node_is_flagged_and_put_back() -> None:
    broken = _edit(_base(), drop=[("guardrail", "router")])
    issue = _codes(broken)["orphan_guardrail"]
    assert "rules and checks don't apply" in issue.message
    assert compile_graph(broken).guardrails.rules == [], "the rules were silently lost"
    fixed = apply_fix(broken, issue.fix)  # type: ignore[arg-type]
    assert compile_graph(fixed).guardrails.rules == ["Be kind."]
    _check_fixes(broken)


@pytest.mark.parametrize(
    ("drop", "code"),
    [
        ([("memory", "agent")], "orphan_memory"),
        ([("router", "agent")], "orphan_router"),
        ([("sub:sql", "agent")], "orphan_subagent"),
        ([(f"db:{DB}", "agent")], "orphan_capability"),
        ([("tool:calculator", "agent")], "orphan_capability"),
        ([(f"src:{SRC}", "kb")], "orphan_data_source"),
        ([("input", "guardrail")], "input_not_wired"),
        ([("agent", "output")], "output_not_wired"),
    ],
)
def test_each_unwired_node_gets_a_fix_that_wires_it(drop: Any, code: str) -> None:
    broken = _edit(_base(), drop=drop)
    issue = _codes(broken)[code]
    assert issue.fix is not None and issue.fix.ops
    _check_fixes(broken)
    assert validate_graph(apply_fix(broken, issue.fix)).warnings == []


def test_an_unwired_subagent_is_not_also_warned_about_its_tools() -> None:
    """It has no database either, but wiring it in comes first: one
    warning, not two."""
    broken = _edit(_base(databases=[]), drop=[("sub:sql", "agent")])
    codes = [w.code for w in validate_graph(broken).warnings]
    assert "orphan_subagent" in codes and "subagent_without_database" not in codes


# ── missing and extra nodes ──────────────────────────────────


@pytest.mark.parametrize("node", ["guardrail", "memory"])
def test_a_missing_optional_node_can_be_added(node: str) -> None:
    broken = _edit(_base(), remove=[node])
    issue = _codes(broken)["defaults_in_use"]
    assert issue.fix is not None and issue.fix.label.startswith("Add a")
    fixed = apply_fix(broken, issue.fix)
    assert "defaults_in_use" not in _codes(fixed)
    assert validate_graph(fixed).ok


@pytest.mark.parametrize("node", ["input", "output"])
def test_a_missing_required_node_can_be_added(node: str) -> None:
    broken = _edit(_base(), remove=[node])
    issue = _codes(broken)["missing_node"]
    fixed = apply_fix(broken, issue.fix)  # type: ignore[arg-type]
    assert validate_graph(fixed).ok and validate_graph(fixed).warnings == []


def test_no_fix_is_offered_for_a_missing_agent() -> None:
    assert _codes(_edit(_base(), remove=["agent"]))["missing_node"].fix is None


def test_duplicates_can_be_removed() -> None:
    graph = _base()
    extra_memory = {
        **next(n for n in graph.model_dump()["nodes"] if n["id"] == "memory"),
        "id": "memory-2",
    }
    extra_calc = {
        **next(n for n in graph.model_dump()["nodes"] if n["id"] == "tool:calculator"),
        "id": "calc-2",
    }
    broken = _edit(graph, extra=[extra_memory, extra_calc], add=[("memory-2", "agent")])
    codes = _codes(broken)
    assert codes["duplicate_node"].fix.label == "Remove the extra memory node"  # type: ignore[union-attr]
    assert codes["duplicate_ref"].fix.label == "Remove this duplicate"  # type: ignore[union-attr]
    _check_fixes(broken)


def test_a_bad_edge_can_be_removed() -> None:
    broken = _edit(_base(), add=[("output", "agent"), ("ghost", "agent")])
    issues = {i.code: i for i in _check_fixes(broken)}
    for code in ("illegal_edge", "dangling_edge"):
        assert issues[code].fix is not None, code
        assert issues[code].fix.label == "Remove this connection"  # type: ignore[union-attr]


def test_invalid_mcp_tool_names_can_be_dropped() -> None:
    graph = _base(mcp_servers=[{"id": MCP, "tools": ["create"]}])
    raw = graph.model_dump()
    for n in raw["nodes"]:
        if n["type"] == "mcp_server":
            n["data"]["tool_allowlist"] = ["create", "bad name!"]
    broken = Graph.model_validate(raw)
    issue = _codes(broken)["invalid_mcp_tool"]
    fixed = apply_fix(broken, issue.fix)  # type: ignore[arg-type]
    node = next(n for n in fixed.nodes if n.type == "mcp_server")
    assert node.data.tool_allowlist == ["create"]  # type: ignore[union-attr]


# ── subagents with nothing to use ────────────────────────────


def test_a_subagent_is_given_what_it_lacks_when_the_canvas_has_it() -> None:
    graph = _base(
        subagents={"sql": True, "research": True},
        tools={"calculator": {"enabled": True}, "web_search": {"enabled": True}},
    )
    broken = _edit(graph, drop=[(f"db:{DB}", "agent")], add=[(f"db:{DB}", "sub:research")])
    issue = _codes(broken)["subagent_without_database"]
    assert issue.fix is not None and issue.fix.label == "Give it the database"
    _check_fixes(broken)


def test_with_nothing_to_give_the_subagent_can_go() -> None:
    broken = _base(databases=[], subagents={"sql": True})
    assert any(n.id == "sub:sql" for n in broken.nodes)
    issue = _codes(broken)["subagent_without_database"]
    assert issue.fix is not None and issue.fix.label == "Remove the subagent"
    _check_fixes(broken)


# ── through the API: references ──────────────────────────────


async def test_reference_issues_come_with_fixes(
    client: AsyncClient, org_headers: dict[str, str]
) -> None:
    a = (
        await client.post("/api/v1/assistants", json={"name": "Fixes"}, headers=org_headers)
    ).json()
    base = f"/api/v1/assistants/{a['id']}"
    conn = await client.post(
        f"{base}/db-connections",
        json={"name": "RO", "engine": "sqlite", "database": "ro.db"},
        headers=org_headers,
    )
    cid = conn.json()["id"]
    graph = a["draft_graph"]
    graph["nodes"] += [
        {
            "id": "db",
            "type": "database",
            "position": {"x": 0, "y": 0},
            "data": {"connection_id": cid, "expose_write": True},
        },
        {
            "id": "ghost",
            "type": "database",
            "position": {"x": 0, "y": 90},
            "data": {"connection_id": "44444444-4444-4444-4444-444444444444"},
        },
    ]
    graph["edges"] += [{"source": "db", "target": "agent"}, {"source": "ghost", "target": "agent"}]
    saved = (await client.put(f"{base}/draft-graph", json=graph, headers=org_headers)).json()
    by_code = {i["code"]: i for i in saved["validation"]["errors"]}
    write = by_code["expose_write_without_write"]["fix"]
    assert write == {
        "label": "Turn off Expose writes",
        "ops": [
            {
                "op": "patch_node",
                "node_id": "db",
                "data": {"expose_write": False},
                "source": None,
                "target": None,
                "node_type": None,
                "position": None,
            }
        ],
    }
    ghost = by_code["unknown_connection"]["fix"]
    assert ghost["label"] == "Remove this node" and ghost["ops"][0]["node_id"] == "ghost"

    # Applied as the web does, then saved: both errors are gone.
    fixed = apply_fix(Graph.model_validate(saved["graph"]), _fix(write))
    fixed = apply_fix(fixed, _fix(ghost))
    resaved = (
        await client.put(
            f"{base}/draft-graph", json=fixed.model_dump(mode="json"), headers=org_headers
        )
    ).json()
    assert resaved["validation"]["errors"] == []


def _fix(raw: dict[str, Any]) -> Any:
    from app.graph.fixes import GraphFix

    return GraphFix.model_validate(raw)


def test_an_edge_drop_that_leaves_nothing_broken_keeps_its_wiring() -> None:
    """Sanity: `_edit` itself leaves a healthy graph healthy."""
    assert validate_graph(_edit(_base())).warnings == []
    assert Edge  # imported for type readers
