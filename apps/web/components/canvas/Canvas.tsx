"use client";

import {
  Background,
  Controls,
  ReactFlow,
  applyEdgeChanges,
  applyNodeChanges,
  type Connection,
  type Edge,
  type EdgeChange,
  type IsValidConnection,
  type Node,
  type NodeChange,
  type NodeMouseHandler,
  type NodeTypes,
} from "@xyflow/react";
import { useTheme } from "next-themes";
import { useCallback, useEffect, useMemo, useState } from "react";

import type { GraphIssues } from "@/components/canvas/graph-sync";
import { StudioNode } from "@/components/canvas/StudioNode";
import { mergeFlowNodes, toFlow } from "@/components/canvas/graph-sync";
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
  highlight = null,
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
  /** Nodes to light up: a run's, or one step's (task 5.9). */
  highlight?: Set<string> | null;
}) {
  const { resolvedTheme } = useTheme();
  const flow = useMemo(
    () => toFlow(graph, issues, sourceLabels ?? {}, highlight),
    [graph, issues, sourceLabels, highlight],
  );

  // React Flow's own copy of the nodes and edges. It reports each node's
  // measured size, drags and selection through onNodesChange / onEdgesChange,
  // and hides a node until it has a size. The canvas used to pass the graph's
  // nodes straight through with no change handler, so every graph update (a
  // delete, a save, data arriving after mount) rebuilt the nodes without a
  // size and left them invisible until a tab switch remounted the canvas.
  const [nodes, setNodes] = useState<Node[]>([]);
  const [edges, setEdges] = useState<Edge[]>([]);

  useEffect(() => {
    setNodes((prev) => mergeFlowNodes(prev, flow.nodes, selectedId));
  }, [flow.nodes, selectedId]);
  useEffect(() => {
    setEdges((prev) => {
      const selected = new Set(prev.filter((e) => e.selected).map((e) => e.id));
      return flow.edges.map((e) => (selected.has(e.id) ? { ...e, selected: true } : e));
    });
  }, [flow.edges]);

  // Removals are not applied here: they go through onNodesDelete /
  // onEdgesDelete to the graph, which is the source of truth, and come back
  // through the effects above.
  const onNodesChange = useCallback(
    (changes: NodeChange[]) =>
      setNodes((ns) =>
        applyNodeChanges(
          changes.filter((c) => c.type !== "remove"),
          ns,
        ),
      ),
    [],
  );
  const onEdgesChange = useCallback(
    (changes: EdgeChange[]) =>
      setEdges((es) =>
        applyEdgeChanges(
          changes.filter((c) => c.type !== "remove"),
          es,
        ),
      ),
    [],
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
    <div className="studio-canvas h-full w-full">
      <ReactFlow
        nodes={nodes}
        edges={edges}
        onNodesChange={onNodesChange}
        onEdgesChange={onEdgesChange}
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
        // Wiring: releasing anywhere within 40px of an input connects to it,
        // and a click on an output then an input works as well as a drag.
        connectionRadius={40}
        connectOnClick
        connectionLineStyle={{ stroke: "var(--primary)", strokeWidth: 2 }}
        defaultEdgeOptions={{ interactionWidth: 24 }}
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
