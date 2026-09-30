import type { Edge, Node } from "@xyflow/react";

import {
  canChangeData,
  problemSummary,
  stationName,
  switchedOffByData,
} from "@/components/canvas/station";
import { nodeFamily, type LineFamily } from "@/components/ui/line-bullet";
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

/** Main-line station types, in route order. Every other type is a
 *  capability joining the Agent (or a subagent) on a branch line. */
export const MAIN_LINE_TYPES: ReadonlySet<string> = new Set([
  "input",
  "guardrail",
  "router",
  "agent",
  "output",
]);
const MAIN_ORDER = ["input", "guardrail", "router", "agent", "output"];

/** Stop numbers for the stations wired onto the main line, from the real
 *  wiring: Input 1, then each main-line station its edges lead to, in
 *  order. Numbers close up when a stop is missing; a station the line
 *  doesn't reach gets none. */
export function mainLineStops(graph: Graph): Map<string, number> {
  const byId = new Map(graph.nodes.map((n) => [n.id, n]));
  const stops = new Map<string, number>();
  let current = graph.nodes.find((n) => n.type === "input");
  while (current && !stops.has(current.id)) {
    stops.set(current.id, stops.size + 1);
    if (current.type === "output") break;
    const from: GraphNode = current;
    current = graph.edges
      .filter((e) => e.source === from.id)
      .map((e) => byId.get(e.target))
      .filter((n): n is GraphNode => Boolean(n) && MAIN_LINE_TYPES.has(n!.type))
      .filter((n) => n.type !== "input" && !stops.has(n.id))
      .sort((a, b) => MAIN_ORDER.indexOf(a.type) - MAIN_ORDER.indexOf(b.type))[0];
  }
  return stops;
}

/** The Agent's three inputs: `left` for the main line, `top` / `bottom` for
 *  capability lines from above or below its row, so they never paint over
 *  the main line. Render-time only: never written to the graph. */
export type AgentHandle = "left" | "top" | "bottom";

export function agentTargetHandle(
  source: Pick<GraphNode, "type" | "position">,
  agent: Pick<GraphNode, "position">,
): AgentHandle {
  if (MAIN_LINE_TYPES.has(source.type)) return "left";
  return source.position.y < agent.position.y ? "top" : "bottom";
}

/** What a station draws with, on top of the graph node itself. */
export type StationData = StudioNodeData & {
  issues?: NodeIssues;
  sourceLabel?: string;
  /** Lit or unlit while a run is shown; undefined otherwise. */
  trace?: "lit" | "dim";
  /** Stop number, for main-line stations the line reaches. */
  stop?: number;
  /** Switched off: hollow bullet, muted name. */
  off?: boolean;
  /** Time the shown run spent here, in ms, when the trace has it. */
  time?: number;
};

/** What a line draws with (`RouteEdge`). */
export type RouteEdgeData = {
  /** `main` when both ends are main-line stations. */
  line: "main" | "capability";
  /** The source station's type: a capability line takes its colour. */
  sourceType: string;
  family: LineFamily;
  /** The first validation message on this edge: drawn dashed, "won't run". */
  problem?: string;
  /** The source can change data (a database with writes exposed). */
  writes?: boolean;
  trace?: "lit" | "dim";
  /** Changes when a run opens, so the lit route draws itself once. */
  drawKey?: string | null;
};

export type FlowOptions = {
  /** Node ids that are switched off (an MCP server turned off). */
  switchedOff?: ReadonlySet<string>;
  /** Node id -> time the shown run spent there, in ms. */
  times?: ReadonlyMap<string, number>;
  /** An id for the run being shown; the lit route draws once per id. */
  drawKey?: string | null;
};

export function toFlow(
  graph: Graph,
  issues: GraphIssues = { nodes: new Map(), edges: new Map() },
  sourceLabels: Record<string, string> = {},
  /** The nodes a run touched (task 5.9): lit, and everything else unlit.
   *  Null when no run is being shown. */
  highlight: Set<string> | null = null,
  options: FlowOptions = {},
) {
  const trace = (id: string) => (highlight ? (highlight.has(id) ? "lit" : "dim") : undefined);
  const stops = mainLineStops(graph);
  const byId = new Map(graph.nodes.map((n) => [n.id, n]));
  const off = (n: GraphNode) => Boolean(options.switchedOff?.has(n.id)) || switchedOffByData(n);
  const nodes: Node<StationData>[] = graph.nodes.map((n) => ({
    id: n.id,
    type: "studio",
    position: n.position,
    deletable: !STRUCTURAL_NODE_TYPES.has(n.type),
    // What a screen reader hears on reaching the station: its name, its
    // kind, where it sits on the pipeline and what is wrong with it.
    ariaLabel: nodeAriaLabel(n, {
      label: stationLabel(n, sourceLabels),
      stop: stops.get(n.id),
      issues: issues.nodes.get(n.id),
      off: off(n),
      dim: trace(n.id) === "dim",
    }),
    data: {
      node: n,
      issues: issues.nodes.get(n.id),
      trace: trace(n.id),
      stop: stops.get(n.id),
      off: off(n),
      time: highlight?.has(n.id) ? options.times?.get(n.id) : undefined,
      // These kinds carry only an id in the graph; the label comes from the
      // assistant's live sources, connections and servers. Resolving
      // `data_source` but not `database` is how a correctly configured
      // database node once read as a removed connection.
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
  const edges: Edge<RouteEdgeData>[] = graph.edges.map((e, i) => {
    // Edge-scoped errors (an illegal connection) used to be dropped on the
    // floor: the web type did not even have the field.
    const problems = issues.edges.get(edgeKey(e.source, e.target));
    const source = byId.get(e.source);
    const target = byId.get(e.target);
    const sourceType = source?.type ?? "";
    const main = MAIN_LINE_TYPES.has(sourceType) && MAIN_LINE_TYPES.has(target?.type ?? "");
    const lit = highlight ? highlight.has(e.source) && highlight.has(e.target) : undefined;
    const writes = source ? canChangeData(source) : false;
    return {
      id: e.id ?? `e-${e.source}-${e.target}-${i}`,
      type: "route",
      source: e.source,
      target: e.target,
      // Named by what it joins, never by raw ids.
      ariaLabel: [
        `Connection from ${source ? stationLabel(source, sourceLabels) : "a removed node"} to ${
          target ? stationLabel(target, sourceLabels) : "a removed node"
        }`,
        writes && !problems ? "can change data" : null,
        problems ? `won't run: ${problems[0]}` : null,
      ]
        .filter(Boolean)
        .join(", "),
      // Render-time only (never persisted): which of the Agent's three
      // inputs this line joins, from where its source sits.
      ...(source && target?.type === "agent"
        ? { targetHandle: agentTargetHandle(source, target) }
        : {}),
      interactionWidth: 16,
      ...(problems && { label: problems[0] }),
      data: {
        line: main ? "main" : "capability",
        sourceType,
        family: main ? "main" : nodeFamily(sourceType),
        problem: problems?.[0],
        writes,
        trace: lit === undefined ? undefined : lit ? "lit" : "dim",
        drawKey: options.drawKey ?? null,
      },
    };
  });
  return { nodes, edges };
}

/** A station's name for people: what it points at ("Shop PG") where it
 *  points at something, else its own name ("Calculator", "Agent"). */
export function stationLabel(
  node: Pick<GraphNode, "type" | "data">,
  sourceLabels: Record<string, string> = {},
): string {
  const ref = String(
    node.data.data_source_id ?? node.data.connection_id ?? node.data.mcp_server_id ?? "",
  );
  return (ref && sourceLabels[ref]) || stationName(node);
}

/** The accessible name of a station on the canvas: "Shop PG, Database, 1
 *  problem: …" or "Agent, step 4 of the pipeline" (the kind is left out
 *  when it is the name). */
export function nodeAriaLabel(
  node: Pick<GraphNode, "type">,
  {
    label,
    stop,
    issues,
    off = false,
    dim = false,
  }: { label: string; stop?: number; issues?: NodeIssues; off?: boolean; dim?: boolean },
): string {
  const kind = NODE_LABEL[node.type] ?? node.type;
  const errors = issues?.errors.length ?? 0;
  const warnings = issues?.warnings.length ?? 0;
  return [
    label,
    kind !== label ? kind : null,
    stop !== undefined ? `step ${stop} of the pipeline` : null,
    off ? "switched off" : null,
    dim ? "not used in this run" : null,
    errors + warnings > 0
      ? `${problemSummary(errors, warnings)}: ${issues!.errors[0] ?? issues!.warnings[0]}`
      : null,
  ]
    .filter(Boolean)
    .join(", ");
}

/** Replace one node's `data` (immutably). */
export function patchNodeData(graph: Graph, nodeId: string, data: Record<string, unknown>): Graph {
  return {
    ...graph,
    nodes: graph.nodes.map((n) => (n.id === nodeId ? { ...n, data: { ...n.data, ...data } } : n)),
  };
}

/** What is wired into a node, by label: for a subagent, the capabilities
 *  that are its own (task 5.10). */
export function wiredInto(
  graph: Graph,
  nodeId: string | null,
  sourceLabels: Record<string, string> = {},
): string[] {
  if (!nodeId) return [];
  const byId = new Map(graph.nodes.map((n) => [n.id, n]));
  return graph.edges
    .filter((e) => e.target === nodeId)
    .map((e) => byId.get(e.source))
    .filter((n): n is GraphNode => Boolean(n) && CAPABILITY_TYPES.has(n!.type))
    .map((n) => {
      const ref = String(n.data.connection_id ?? n.data.mcp_server_id ?? "");
      const detail = n.type === "tool" ? String(n.data.key ?? "") : sourceLabels[ref];
      return detail ? `${NODE_LABEL[n.type] ?? n.type}: ${detail}` : (NODE_LABEL[n.type] ?? n.type);
    });
}

const CAPABILITY_TYPES = new Set(["knowledge_base", "database", "tool", "mcp_server"]);

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

/** The snap grid: 24px, the same as the canvas dots. */
export const GRID = 24;
const snap = (v: number) => Math.round(v / GRID) * GRID;

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
/** Four grid cells: a 52px station plus room for lines between rows. */
const ROW_GAP = 96;
/** The order of branch stations within a column. */
const BRANCH_ORDER = [
  "knowledge_base",
  "memory",
  "database",
  "tool",
  "mcp_server",
  "data_source",
  "subagent",
];
const rank = (t: string, order: string[]) => {
  const i = order.indexOf(t);
  return i === -1 ? order.length : i;
};
/** Rows -start, +start, -(start+1), +(start+1) ...: balanced within one. */
const alternate = (i: number, start: number) =>
  (i % 2 === 0 ? -1 : 1) * (start + Math.floor(i / 2));

/** Room a station needs: wider than most plates, one row tall plus space
 *  for lines between rows. */
const CLEAR_X = 200;
const CLEAR_Y = 76;
const overlaps = (a: { x: number; y: number }, b: { x: number; y: number }) =>
  Math.abs(a.x - b.x) < CLEAR_X && Math.abs(a.y - b.y) < CLEAR_Y;

/** Clears a spot for a main-line station about to land there (the router,
 *  on the Agent's row). Graphs laid out before the main line had a row of
 *  its own put capabilities on it, so a new station would stack on top of
 *  one. Any capability station in the way moves to the nearest free row in
 *  its own column (above first, then below), on the grid. Main-line
 *  stations never move; nothing else about the graph changes. */
export function makeRoom(graph: Graph, at: { x: number; y: number }): Graph {
  let nodes = graph.nodes;
  for (const n of graph.nodes) {
    if (MAIN_LINE_TYPES.has(n.type) || !overlaps(n.position, at)) continue;
    for (let i = 0; ; i++) {
      const spot = { x: n.position.x, y: snap(at.y + alternate(i, 1) * ROW_GAP) };
      if (nodes.some((o) => o.id !== n.id && overlaps(o.position, spot))) continue;
      nodes = nodes.map((o) => (o.id === n.id ? { ...o, position: spot } : o));
      break;
    }
  }
  return nodes === graph.nodes ? graph : { ...graph, nodes };
}

/** Roughly below-left of the agent, so a freshly added capability node lands
 *  somewhere sensible instead of on top of whatever is at the origin. Below
 *  the anchor's row, never on it, and on the grid. */
export function placeNear(graph: Graph, anchorType: string, index: number) {
  const anchor = graph.nodes.find((n) => n.type === anchorType);
  const base = anchor?.position ?? { x: 0, y: 0 };
  return { x: snap(base.x - 264), y: snap(base.y + 144 + index * ROW_GAP) };
}

/** Auto-layout (task 1.14; docs/DESIGN.md section 6). Every node goes to its
 *  type's column. The main line (Input, Guardrails, Router, Agent, Output)
 *  sits on one row, row 0, so it runs straight, and a capability station
 *  never lands on it. Branch columns fill rows -1, +1, -2, +2 and so on,
 *  except that data sources stay on their knowledge base's side (so their
 *  lines don't cross the main line) and subagents start outside the
 *  capability rows (so capability lines into the Agent don't run through
 *  them). Rows are 96px apart, on the 24px grid. Deterministic, so pressing
 *  it twice is a no-op, and it never touches wiring or data, only
 *  positions. */
export function tidyLayout(graph: Graph): Graph {
  const columns = new Map<number, GraphNode[]>();
  for (const n of graph.nodes) {
    const x = COLUMN[n.type] ?? COLUMN.knowledge_base;
    columns.set(x, [...(columns.get(x) ?? []), n]);
  }
  const row = new Map<string, number>();
  const place = (x: number) => {
    const ns = [...(columns.get(x) ?? [])].sort(
      (a, b) =>
        rank(a.type, MAIN_ORDER) - rank(b.type, MAIN_ORDER) ||
        rank(a.type, BRANCH_ORDER) - rank(b.type, BRANCH_ORDER) ||
        a.id.localeCompare(b.id),
    );
    const mains = ns.filter((n) => MAIN_LINE_TYPES.has(n.type));
    if (mains.length > 0) row.set(mains[0].id, 0);
    // A second main-line station in one column (an invalid graph) branches
    // off like anything else rather than piling onto row 0.
    const branches = [...mains.slice(1), ...ns.filter((n) => !MAIN_LINE_TYPES.has(n.type))];
    let start = 1;
    let side = 0;
    if (x === COLUMN.subagent) {
      const caps = (columns.get(COLUMN.knowledge_base) ?? []).map((n) =>
        Math.abs(row.get(n.id) ?? 0),
      );
      start = Math.max(0, ...caps) + 1;
    }
    if (x === COLUMN.data_source) {
      const kb = graph.nodes.find((n) => n.type === "knowledge_base");
      side = Math.sign(kb ? (row.get(kb.id) ?? 0) : 0);
    }
    branches.forEach((n, i) =>
      row.set(n.id, side !== 0 ? side * (start + i) : alternate(i, start)),
    );
  };
  // The capability column first: data sources follow the knowledge base's
  // side, and subagents start outside its deepest row.
  const order = [
    COLUMN.knowledge_base,
    ...[...columns.keys()].filter((x) => x !== COLUMN.knowledge_base),
  ];
  for (const x of order) if (columns.has(x)) place(x);

  const pos = new Map<string, { x: number; y: number }>();
  for (const [x, ns] of columns) {
    for (const n of ns) pos.set(n.id, { x, y: (row.get(n.id) ?? 0) * ROW_GAP });
  }
  return { ...graph, nodes: graph.nodes.map((n) => ({ ...n, position: pos.get(n.id)! })) };
}
