import type { Graph, GraphFix, GraphNode } from "@/lib/api";

/** A suggested fix applied to the canvas (task 5.11): the same few
 *  operations, with the same meaning, as the API's `apply_fix`
 *  (`app/graph/fixes.py`). The result is saved the normal way, so the
 *  server validates it like any other edit. */
export function applyFix(graph: Graph, fix: GraphFix): Graph {
  let nodes = [...graph.nodes];
  let edges = [...graph.edges];
  for (const op of fix.ops) {
    if (op.op === "add_edge" && op.source && op.target) {
      if (!edges.some((e) => e.source === op.source && e.target === op.target)) {
        edges.push({ source: op.source, target: op.target });
      }
    } else if (op.op === "remove_edge") {
      edges = edges.filter((e) => !(e.source === op.source && e.target === op.target));
    } else if (op.op === "remove_node") {
      nodes = nodes.filter((n) => n.id !== op.node_id);
      edges = edges.filter((e) => e.source !== op.node_id && e.target !== op.node_id);
    } else if (op.op === "patch_node") {
      nodes = nodes.map((n) =>
        n.id === op.node_id ? { ...n, data: { ...n.data, ...(op.data ?? {}) } } : n,
      );
    } else if (op.op === "add_node" && op.node_id && op.node_type) {
      const added: GraphNode = {
        id: op.node_id,
        type: op.node_type,
        position: { x: op.position?.x ?? 0, y: op.position?.y ?? 0 },
        data: op.data ?? {},
      };
      nodes.push(added);
    }
  }
  return { ...graph, nodes, edges };
}

/** The node a fix is about, to select once it's applied: an added node,
 *  else the one it changed; none when it removed it. */
export function fixFocus(fix: GraphFix): string | null {
  const added = fix.ops.find((o) => o.op === "add_node");
  if (added?.node_id) return added.node_id;
  const patched = fix.ops.find((o) => o.op === "patch_node");
  return patched?.node_id ?? null;
}
