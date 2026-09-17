import type { Edge, Node } from "@xyflow/react";

import type { Graph, GraphNode } from "@/lib/api";

export type StudioNodeData = { node: GraphNode; selected?: boolean };

export function toFlow(graph: Graph, invalidNodeIds: Set<string> = new Set()) {
  const nodes: Node<StudioNodeData & { invalid?: boolean }>[] = graph.nodes.map((n) => ({
    id: n.id,
    type: "studio",
    position: n.position,
    data: { node: n, invalid: invalidNodeIds.has(n.id) },
  }));
  const edges: Edge[] = graph.edges.map((e, i) => ({
    id: e.id ?? `e-${e.source}-${e.target}-${i}`,
    source: e.source,
    target: e.target,
    animated: false,
  }));
  return { nodes, edges };
}

/** Merge new React Flow positions back into a graph. */
export function applyPositions(graph: Graph, flowNodes: Node[]): Graph {
  const pos = new Map(flowNodes.map((n) => [n.id, n.position]));
  return {
    ...graph,
    nodes: graph.nodes.map((n) => ({ ...n, position: pos.get(n.id) ?? n.position })),
  };
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
