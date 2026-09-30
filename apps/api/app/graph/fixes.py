"""One-click fixes for graph issues (task 5.11).

A validation issue can carry a suggested fix: a label ("Wire it to the
agent") and a few plain graph operations. The web applies the operations
to the canvas and saves the graph the normal way, so the server validates
the result like any other edit; nothing here writes anything.

Operations, deliberately few and dumb, so the web's copy
(`lib/graph-fixes.ts`) can't drift in meaning:

- `add_edge` / `remove_edge` (`source`, `target`);
- `remove_node` (`node_id`, and every edge touching it);
- `patch_node` (`node_id`, `data`: merged into the node's data);
- `add_node` (`node_id`, `node_type`, `data`, `position`).

`apply_fix` is the reference implementation; the tests use it to check that
each suggested fix really clears its issue.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import Field

from app.graph.nodes import Graph
from app.schemas.common import ApiModel


class FixOp(ApiModel):
    op: Literal["add_edge", "remove_edge", "remove_node", "patch_node", "add_node"]
    source: str | None = None
    target: str | None = None
    node_id: str | None = None
    node_type: str | None = None
    data: dict[str, Any] | None = None
    position: dict[str, float] | None = None


class GraphFix(ApiModel):
    """A suggested fix: what the button says, and what it does."""

    label: str
    ops: list[FixOp] = Field(default_factory=list)


def wire(label: str, *pairs: tuple[str, str]) -> GraphFix:
    return GraphFix(label=label, ops=[FixOp(op="add_edge", source=s, target=t) for s, t in pairs])


def remove_node(node_id: str, label: str = "Remove this node") -> GraphFix:
    return GraphFix(label=label, ops=[FixOp(op="remove_node", node_id=node_id)])


def remove_edge(source: str, target: str) -> GraphFix:
    return GraphFix(
        label="Remove this connection",
        ops=[FixOp(op="remove_edge", source=source, target=target)],
    )


def patch(node_id: str, data: dict[str, Any], label: str) -> GraphFix:
    return GraphFix(label=label, ops=[FixOp(op="patch_node", node_id=node_id, data=data)])


def free_id(graph: Graph, base: str) -> str:
    """`base`, or `base-2`, `base-3`... whichever the graph doesn't use."""
    taken = {n.id for n in graph.nodes}
    if base not in taken:
        return base
    n = 2
    while f"{base}-{n}" in taken:
        n += 1
    return f"{base}-{n}"


def apply_fix(graph: Graph, fix: GraphFix) -> Graph:
    """The graph with the fix applied (the web does the same)."""
    raw = graph.model_dump()
    nodes: list[dict[str, Any]] = raw["nodes"]
    edges: list[dict[str, Any]] = raw["edges"]
    for op in fix.ops:
        if op.op == "add_edge" and not any(
            e["source"] == op.source and e["target"] == op.target for e in edges
        ):
            edges.append({"source": op.source, "target": op.target})
        elif op.op == "remove_edge":
            edges[:] = [
                e for e in edges if not (e["source"] == op.source and e["target"] == op.target)
            ]
        elif op.op == "remove_node":
            nodes[:] = [n for n in nodes if n["id"] != op.node_id]
            edges[:] = [e for e in edges if op.node_id not in (e["source"], e["target"])]
        elif op.op == "patch_node":
            for n in nodes:
                if n["id"] == op.node_id:
                    n["data"] = {**n["data"], **(op.data or {})}
        elif op.op == "add_node":
            nodes.append(
                {
                    "id": op.node_id,
                    "type": op.node_type,
                    "position": op.position or {"x": 0.0, "y": 0.0},
                    "data": op.data or {},
                }
            )
    return Graph.model_validate(raw)


__all__ = [
    "FixOp",
    "GraphFix",
    "apply_fix",
    "free_id",
    "patch",
    "remove_edge",
    "remove_node",
    "wire",
]
