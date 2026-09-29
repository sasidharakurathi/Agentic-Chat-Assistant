import type { Edge, Node } from "@xyflow/react";

import type { Graph, GraphNode, ValidationResult } from "@/lib/api";

export type StudioNodeData = { node: GraphNode; selected?: boolean };

/** Node types every pipeline has exactly one of, which cannot be deleted.
 *
 *  input/agent/output hold the pipeline together. guardrail and memory are
 *  here too: the palette cannot add them back, and deleting one silently
 *  reset its settings to the schema defaults. The drawer refused to delete
 *  input/agent/output, but the canvas's Delete key did not ask, so pressing
 *  it with the agent selected removed the agent. One list, enforced in
 *  `removeNodes`, `toFlow` (React Flow's own `deletable`) and the drawer. */
export const STRUCTURAL_NODE_TYPES: ReadonlySet<string> = new Set([
  "input",
  "agent",
  "output",
  "guardrail",
  "memory",
]);

/** Validation messages attached to one node, or one edge. */
export type NodeIssues = { errors: string[]; warnings: string[] };
export type GraphIssues = {
  nodes: Map<string, NodeIssues>;
  /** Keyed `source>target`. */
  edges: Map<string, string[]>;
};

export const edgeKey = (source: string, target: string) => `${source}>${target}`;

/** Index a validation result by where each issue points. */
export function indexIssues(validation: ValidationResult): GraphIssues {
  const nodes = new Map<string, NodeIssues>();
  const edges = new Map<string, string[]>();
  const at = (id: string) => {
    let v = nodes.get(id);
    if (!v) nodes.set(id, (v = { errors: [], warnings: [] }));
    return v;
  };
  for (const e of validation.errors) {
    if (e.node_id) at(e.node_id).errors.push(e.message);
    if (e.edge) {
      const k = edgeKey(e.edge[0], e.edge[1]);
      edges.set(k, [...(edges.get(k) ?? []), e.message]);
    }
  }
  // Warnings carry node ids too, and were never shown on the canvas at all.
  for (const w of validation.warnings) if (w.node_id) at(w.node_id).warnings.push(w.message);
  return { nodes, edges };
}

/** The graph's nodes, as React Flow should hold them after a graph update.
 *
 *  React Flow hides a node until it knows its size, and learns the size once,
 *  when the node first renders. A graph update builds fresh node objects
 *  without one, so each node keeps the size React Flow measured before (and
 *  selection follows `selectedId`). Removed nodes drop out; new ones are
 *  measured on first render as usual. */
export function mergeFlowNodes<T extends Node>(
  prev: Node[],
  next: T[],
  selectedId: string | null,
): T[] {
  const measured = new Map(prev.map((n) => [n.id, n.measured]));
  return next.map((n) => {
    const size = measured.get(n.id);
    return { ...n, selected: n.id === selectedId, ...(size ? { measured: size } : {}) };
  });
}

export function toFlow(
  graph: Graph,
  issues: GraphIssues = { nodes: new Map(), edges: new Map() },
  sourceLabels: Record<string, string> = {},
) {
  const nodes: Node<StudioNodeData & { issues?: NodeIssues; sourceLabel?: string }>[] =
    graph.nodes.map((n) => ({
      id: n.id,
      type: "studio",
      position: n.position,
      deletable: !STRUCTURAL_NODE_TYPES.has(n.type),
      data: {
        node: n,
        issues: issues.nodes.get(n.id),
        // Both kinds carry only an id in the graph; the label comes from the
        // assistant's live sources/connections. Resolving `data_source` but
        // not `database` is how a correctly configured database node ends up
        // reading "unknown connection" on the canvas.
        sourceLabel:
          n.type === "data_source"
            ? sourceLabels[String(n.data.data_source_id ?? "")]
            : n.type === "database"
              ? sourceLabels[String(n.data.connection_id ?? "")]
              : n.type === "mcp_server"
                ? sourceLabels[String(n.data.mcp_server_id ?? "")]
                : undefined,
      },
    }));
  const edges: Edge[] = graph.edges.map((e, i) => {
    // Edge-scoped errors (an illegal connection) used to be dropped on the
    // floor: the web type did not even have the field.
    const problems = issues.edges.get(edgeKey(e.source, e.target));
    return {
      id: e.id ?? `e-${e.source}-${e.target}-${i}`,
      source: e.source,
      target: e.target,
      animated: false,
      ...(problems && {
        style: { stroke: "var(--destructive)", strokeWidth: 2 },
        label: problems[0],
        labelStyle: { fill: "var(--destructive)", fontSize: 11 },
      }),
    };
  });
  return { nodes, edges };
}

/** Replace one node's `data` (immutably). */
export function patchNodeData(graph: Graph, nodeId: string, data: Record<string, unknown>): Graph {
  return {
    ...graph,
    nodes: graph.nodes.map((n) => (n.id === nodeId ? { ...n, data: { ...n.data, ...data } } : n)),
  };
}

export const NODE_LABEL: Record<string, string> = {
  input: "Input",
  output: "Output",
  guardrail: "Guardrails",
  router: "Router",
  knowledge_base: "Knowledge base",
  data_source: "Data source",
  database: "Database",
  tool: "Tool",
  mcp_server: "MCP server",
  subagent: "Subagent",
  agent: "Agent",
  memory: "Memory",
};

/** Next free id for a node type — `kb`, `kb-2`, `kb-3`, … */
export function nextNodeId(graph: Graph, prefix: string): string {
  if (!graph.nodes.some((n) => n.id === prefix)) return prefix;
  for (let i = 2; ; i++) {
    const candidate = `${prefix}-${i}`;
    if (!graph.nodes.some((n) => n.id === candidate)) return candidate;
  }
}

export function addNode(graph: Graph, node: GraphNode): Graph {
  return { ...graph, nodes: [...graph.nodes, node] };
}

export function addEdge(graph: Graph, source: string, target: string): Graph {
  if (graph.edges.some((e) => e.source === source && e.target === target)) return graph;
  return { ...graph, edges: [...graph.edges, { source, target }] };
}

/** Remove nodes *and* every edge touching them — an edge pointing at a node
 *  that no longer exists fails backend validation as `unknown_edge_endpoint`,
 *  so dropping them together is what keeps the graph saveable. */
export function removeNodes(graph: Graph, ids: string[]): Graph {
  const structural = new Set(
    graph.nodes.filter((n) => STRUCTURAL_NODE_TYPES.has(n.type)).map((n) => n.id),
  );
  const gone = new Set(ids.filter((id) => !structural.has(id)));
  return {
    ...graph,
    nodes: graph.nodes.filter((n) => !gone.has(n.id)),
    edges: graph.edges.filter((e) => !gone.has(e.source) && !gone.has(e.target)),
  };
}

export function removeEdges(graph: Graph, pairs: { source: string; target: string }[]): Graph {
  const gone = new Set(pairs.map((p) => `${p.source}>${p.target}`));
  return { ...graph, edges: graph.edges.filter((e) => !gone.has(`${e.source}>${e.target}`)) };
}

/** Roughly below-left of the agent, so a freshly added capability node lands
 *  somewhere sensible instead of on top of whatever is at the origin. */
export function placeNear(graph: Graph, anchorType: string, index: number) {
  const anchor = graph.nodes.find((n) => n.type === anchorType);
  const base = anchor?.position ?? { x: 0, y: 0 };
  return { x: base.x - 260, y: base.y + 140 + index * 90 };
}

// Column per node type: the same left-to-right reading order as the
// backend's projection (app/graph/project.py), so a tidied graph looks like a
// freshly generated one.
const COLUMN: Record<string, number> = {
  input: 0,
  guardrail: 260,
  data_source: 380,
  knowledge_base: 640,
  database: 640,
  tool: 640,
  mcp_server: 640,
  memory: 640,
  subagent: 820,
  router: 820,
  agent: 1040,
  output: 1320,
};
const ROW_GAP = 110;

/** Auto-layout (task 1.14): every node to its type's column, stacked and
 *  centred on the agent's row. Deterministic, so pressing it twice is a
 *  no-op, and it never touches wiring or data, only positions. */
export function tidyLayout(graph: Graph): Graph {
  const columns = new Map<number, GraphNode[]>();
  for (const n of graph.nodes) {
    const x = COLUMN[n.type] ?? 640;
    columns.set(x, [...(columns.get(x) ?? []), n]);
  }
  const pos = new Map<string, { x: number; y: number }>();
  for (const [x, ns] of columns) {
    const sorted = [...ns].sort((a, b) => a.type.localeCompare(b.type) || a.id.localeCompare(b.id));
    const top = -((sorted.length - 1) * ROW_GAP) / 2;
    sorted.forEach((n, i) => pos.set(n.id, { x, y: top + i * ROW_GAP }));
  }
  return { ...graph, nodes: graph.nodes.map((n) => ({ ...n, position: pos.get(n.id)! })) };
}
