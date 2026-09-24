"""Small structural diffs for the version-compare view.

Not a general-purpose differ: it produces flat, dotted-path change lists that a
UI can render as a table.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import Field

from app.schemas.common import ApiModel

DiffOp = Literal["added", "removed", "changed"]

_SENTINEL = object()


class DiffEntry(ApiModel):
    path: str
    op: DiffOp
    before: Any = None
    after: Any = None


class GraphDiff(ApiModel):
    nodes_added: list[str] = Field(default_factory=list)
    nodes_removed: list[str] = Field(default_factory=list)
    nodes_changed: list[DiffEntry] = Field(default_factory=list)
    edges_added: list[tuple[str, str]] = Field(default_factory=list)
    edges_removed: list[tuple[str, str]] = Field(default_factory=list)
    #: id -> node type for every node mentioned above, so a UI can say
    #: "database node added" rather than show a bare canvas id like "db".
    node_types: dict[str, str] = Field(default_factory=dict)


def diff_values(before: Any, after: Any, path: str = "") -> list[DiffEntry]:
    """Recursive diff of two JSON-ish values into dotted-path entries."""
    if isinstance(before, dict) and isinstance(after, dict):
        out: list[DiffEntry] = []
        for key in sorted(set(before) | set(after)):
            child = f"{path}.{key}" if path else str(key)
            b = before.get(key, _SENTINEL)
            a = after.get(key, _SENTINEL)
            if b is _SENTINEL:
                out.append(DiffEntry(path=child, op="added", after=a))
            elif a is _SENTINEL:
                out.append(DiffEntry(path=child, op="removed", before=b))
            else:
                out.extend(diff_values(b, a, child))
        return out
    if isinstance(before, list) and isinstance(after, list):
        if before == after:
            return []
        # Lists in the config are canonicalised (sorted), so treat a changed list
        # as one atomic change rather than trying to align elements.
        return [DiffEntry(path=path, op="changed", before=before, after=after)]
    if before != after:
        return [DiffEntry(path=path, op="changed", before=before, after=after)]
    return []


def diff_graphs(before: dict[str, Any], after: dict[str, Any]) -> GraphDiff:
    b_nodes = {n["id"]: n for n in before.get("nodes", [])}
    a_nodes = {n["id"]: n for n in after.get("nodes", [])}
    d = GraphDiff(
        nodes_added=sorted(set(a_nodes) - set(b_nodes)),
        nodes_removed=sorted(set(b_nodes) - set(a_nodes)),
    )
    for nid in sorted(set(a_nodes) & set(b_nodes)):
        bn, an = b_nodes[nid], a_nodes[nid]
        if bn.get("type") != an.get("type") or bn.get("data") != an.get("data"):
            d.nodes_changed.extend(diff_values(bn.get("data"), an.get("data"), nid))

    b_edges = {(e["source"], e["target"]) for e in before.get("edges", [])}
    a_edges = {(e["source"], e["target"]) for e in after.get("edges", [])}
    d.edges_added = sorted(a_edges - b_edges)
    d.edges_removed = sorted(b_edges - a_edges)
    mentioned = {*d.nodes_added, *d.nodes_removed}
    mentioned |= {e.path.split(".", 1)[0] for e in d.nodes_changed}
    mentioned |= {nid for edge in (*d.edges_added, *d.edges_removed) for nid in edge}
    both = {**b_nodes, **a_nodes}
    d.node_types = {nid: str(both[nid].get("type")) for nid in sorted(mentioned) if nid in both}
    return d


__all__ = ["DiffEntry", "DiffOp", "GraphDiff", "diff_graphs", "diff_values"]
