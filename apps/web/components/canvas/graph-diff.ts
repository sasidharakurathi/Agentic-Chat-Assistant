import {
  edgeKey,
  tidyLayout,
  type DiffMark,
  type LineDiffMark,
} from "@/components/canvas/graph-sync";
import type { Graph, VersionDiff } from "@/lib/api";

/** Two versions as one drawing (task 6.8): everything in the newer version,
 *  plus what the older one had and the newer does not, each change marked. */
export type GraphComparison = {
  /** The newer graph with the removed stations and lines put back, laid
   *  out afresh. */
  graph: Graph;
  nodes: Map<string, DiffMark>;
  /** Keyed `source>target`. */
  edges: Map<string, LineDiffMark>;
  /** Node id -> how many of its settings changed. */
  counts: Map<string, number>;
};

/** The node a changed setting belongs to. The server reports a change as a
 *  path, `<node id>.<setting>`, and a node id may itself hold a dot, so the
 *  longest id that fits wins. */
function ownerOf(path: string, ids: string[]): string | null {
  let best: string | null = null;
  for (const id of ids) {
    if ((path === id || path.startsWith(`${id}.`)) && id.length > (best?.length ?? -1)) best = id;
  }
  return best;
}

/** Build the comparison from the two graphs and the server's diff of them.
 *
 *  The drawing is tidied, not shown as either version was arranged: the
 *  two were laid out by hand at different times, a removed station's old
 *  spot may now hold something else, and a comparison is about what is
 *  connected, not where it was dragged to. Tidying is deterministic, so
 *  the same two versions always draw the same way. */
export function compareGraphs(
  from: Graph,
  to: Graph,
  diff: VersionDiff["graph_diff"],
): GraphComparison {
  const removedIds = new Set(diff.nodes_removed);
  const inTo = new Set(to.nodes.map((n) => n.id));
  const removedNodes = from.nodes.filter((n) => removedIds.has(n.id) && !inTo.has(n.id));
  const toEdges = new Set(to.edges.map((e) => edgeKey(e.source, e.target)));
  const removedEdges = diff.edges_removed
    .filter(([s, t]) => !toEdges.has(edgeKey(s, t)))
    .map(([source, target]) => ({ source, target }));
  const graph = tidyLayout({
    ...to,
    nodes: [...to.nodes, ...removedNodes],
    edges: [...to.edges, ...removedEdges],
  });

  const nodes = new Map<string, DiffMark>();
  const counts = new Map<string, number>();
  const ids = graph.nodes.map((n) => n.id);
  for (const entry of diff.nodes_changed) {
    const id = ownerOf(entry.path, ids);
    if (!id) continue;
    nodes.set(id, "changed");
    counts.set(id, (counts.get(id) ?? 0) + 1);
  }
  // Added and removed win over changed: a station that is new has no
  // "before" to have changed from.
  for (const id of diff.nodes_added) nodes.set(id, "added");
  for (const id of diff.nodes_removed) nodes.set(id, "removed");
  for (const id of [...diff.nodes_added, ...diff.nodes_removed]) counts.delete(id);

  const edges = new Map<string, LineDiffMark>();
  for (const [s, t] of diff.edges_added) edges.set(edgeKey(s, t), "added");
  for (const [s, t] of diff.edges_removed) edges.set(edgeKey(s, t), "removed");
  return { graph, nodes, edges, counts };
}

/** "2 added, 1 removed, 3 changed", or null when the pipelines are the
 *  same. Stations and lines are counted together: both are things on the
 *  drawing that differ. */
export function comparisonSummary(c: GraphComparison): string | null {
  const count = { added: 0, removed: 0, changed: 0 };
  for (const mark of c.nodes.values()) count[mark] += 1;
  for (const mark of c.edges.values()) count[mark] += 1;
  const parts = (["added", "removed", "changed"] as const)
    .filter((k) => count[k] > 0)
    .map((k) => `${count[k]} ${k}`);
  return parts.length > 0 ? parts.join(", ") : null;
}

/** The changed settings that belong to one station. */
export function changesFor<T extends { path: string }>(
  entries: T[],
  nodeId: string,
  graph: Graph,
): T[] {
  const ids = graph.nodes.map((n) => n.id);
  return entries.filter((e) => ownerOf(e.path, ids) === nodeId);
}
