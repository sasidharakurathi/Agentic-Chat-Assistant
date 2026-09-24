"use client";

import {
  Background,
  Controls,
  ReactFlow,
  type Connection,
  type Edge,
  type IsValidConnection,
  type Node,
  type NodeMouseHandler,
  type NodeTypes,
} from "@xyflow/react";
import { useTheme } from "next-themes";
import { useCallback, useMemo } from "react";

import type { GraphIssues } from "@/components/canvas/graph-sync";
import { StudioNode } from "@/components/canvas/StudioNode";
import { toFlow } from "@/components/canvas/graph-sync";
import type { Graph, GraphSchema } from "@/lib/api";

const nodeTypes: NodeTypes = { studio: StudioNode };

export function Canvas({
  graph,
  schema,
  issues,
  sourceLabels,
  selectedId,
  onSelect,
  onNodeMoved,
  onConnect,
  onDeleteNodes,
  onDeleteEdges,
}: {
  graph: Graph;
  /** Wiring rules from `GET /meta/graph-schema` — the validator's own
   *  constants, so the canvas can't drift from what the API will accept. */
  schema: GraphSchema | null;
  issues?: GraphIssues;
  /** data_source_id -> human name, so a data source node reads as
   *  "Refund Policy" rather than a uuid fragment. */
  sourceLabels?: Record<string, string>;
  selectedId: string | null;
  onSelect: (id: string | null) => void;
  onNodeMoved: (id: string, position: { x: number; y: number }) => void;
  onConnect?: (source: string, target: string) => void;
  onDeleteNodes?: (ids: string[]) => void;
  onDeleteEdges?: (edges: { source: string; target: string }[]) => void;
}) {
  const { resolvedTheme } = useTheme();
  const { nodes, edges } = useMemo(
    () => toFlow(graph, issues, sourceLabels ?? {}),
    [graph, issues, sourceLabels],
  );

  const styled = useMemo(
    () => nodes.map((n) => ({ ...n, selected: n.id === selectedId })),
    [nodes, selectedId],
  );

  const typeOf = useMemo(() => new Map(graph.nodes.map((n) => [n.id, n.type])), [graph.nodes]);
  const allowed = useMemo(
    () => new Set((schema?.allowed_edges ?? []).map(([a, b]) => `${a}>${b}`)),
    [schema],
  );

  const isValidConnection: IsValidConnection = useCallback(
    (c: Connection | Edge) => {
      if (!c.source || !c.target || c.source === c.target) return false;
      const from = typeOf.get(c.source);
      const to = typeOf.get(c.target);
      if (!from || !to) return false;
      // Already wired? Offering a duplicate edge only creates noise.
      if (graph.edges.some((e) => e.source === c.source && e.target === c.target)) return false;
      // No schema yet (still loading): fall back to permissive, since the API
      // validates anyway and a blocked-but-legal drag is the worse failure.
      if (!schema) return true;
      return allowed.has(`${from}>${to}`);
    },
    [typeOf, allowed, schema, graph.edges],
  );

  const handleNodeClick: NodeMouseHandler = (_, node) => onSelect(node.id);

  return (
    <div className="h-full w-full">
      <ReactFlow
        nodes={styled}
        edges={edges}
        nodeTypes={nodeTypes}
        onNodeClick={handleNodeClick}
        onPaneClick={() => onSelect(null)}
        onNodeDragStop={(_, node) => onNodeMoved(node.id, node.position)}
        onConnect={(c) => c.source && c.target && onConnect?.(c.source, c.target)}
        isValidConnection={isValidConnection}
        onNodesDelete={(ns: Node[]) => onDeleteNodes?.(ns.map((n) => n.id))}
        onEdgesDelete={(es: Edge[]) =>
          onDeleteEdges?.(es.map((e) => ({ source: e.source, target: e.target })))
        }
        colorMode={resolvedTheme === "dark" ? "dark" : "light"}
        fitView
        proOptions={{ hideAttribution: true }}
        nodesConnectable={Boolean(onConnect)}
        deleteKeyCode={onDeleteNodes ? ["Backspace", "Delete"] : null}
      >
        <Background gap={16} />
        <Controls showInteractive={false} />
      </ReactFlow>
    </div>
  );
}
