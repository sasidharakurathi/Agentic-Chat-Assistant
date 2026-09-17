"use client";

import {
  Background,
  Controls,
  ReactFlow,
  type NodeMouseHandler,
  type NodeTypes,
} from "@xyflow/react";
import { useTheme } from "next-themes";
import { useMemo } from "react";

import { StudioNode } from "@/components/canvas/StudioNode";
import { toFlow } from "@/components/canvas/graph-sync";
import type { Graph } from "@/lib/api";

const nodeTypes: NodeTypes = { studio: StudioNode };

export function Canvas({
  graph,
  invalidNodeIds,
  selectedId,
  onSelect,
  onNodeMoved,
}: {
  graph: Graph;
  invalidNodeIds?: Set<string>;
  selectedId: string | null;
  onSelect: (id: string | null) => void;
  onNodeMoved: (id: string, position: { x: number; y: number }) => void;
}) {
  const { resolvedTheme } = useTheme();
  const { nodes, edges } = useMemo(
    () => toFlow(graph, invalidNodeIds ?? new Set()),
    [graph, invalidNodeIds],
  );

  const styled = useMemo(
    () => nodes.map((n) => ({ ...n, selected: n.id === selectedId })),
    [nodes, selectedId],
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
        colorMode={resolvedTheme === "dark" ? "dark" : "light"}
        fitView
        proOptions={{ hideAttribution: true }}
        nodesConnectable={false}
        deleteKeyCode={null}
      >
        <Background gap={16} />
        <Controls showInteractive={false} />
      </ReactFlow>
    </div>
  );
}
