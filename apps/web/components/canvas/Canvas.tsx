"use client";

import {
  Background,
  Controls,
  ReactFlow,
  applyEdgeChanges,
  applyNodeChanges,
  type AriaLabelConfig,
  type Connection,
  type Edge,
  type EdgeChange,
  type EdgeTypes,
  type IsValidConnection,
  type Node,
  type NodeChange,
  type NodeMouseHandler,
  type NodeTypes,
  type OnSelectionChangeFunc,
  type ReactFlowInstance,
} from "@xyflow/react";
import { useTheme } from "next-themes";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { CanvasKey } from "@/components/canvas/CanvasKey";
import { CanvasViewContext, prefersReducedMotion } from "@/components/canvas/canvas-view";
import type { FlowOptions, GraphIssues } from "@/components/canvas/graph-sync";
import { GRID, mergeFlowEdges, mergeFlowNodes, toFlow } from "@/components/canvas/graph-sync";
import { RouteConnectionLine, RouteEdge } from "@/components/canvas/RouteEdge";
import { StudioNode } from "@/components/canvas/StudioNode";
import type { LineFamily } from "@/components/ui/line-bullet";
import type { Graph, GraphSchema } from "@/lib/api";

const nodeTypes: NodeTypes = { studio: StudioNode };

/** React Flow's own words for its controls and keyboard help, in sentence
 *  case and plain words. */
const ARIA_LABELS: Partial<AriaLabelConfig> = {
  "node.a11yDescription.default":
    "Press Enter to open its settings. Press Delete to remove it, or Escape to cancel.",
  "node.a11yDescription.keyboardDisabled":
    "Press Enter to open its settings. The arrow keys then move it. Press Delete to remove it, or Escape to cancel.",
  "node.a11yDescription.ariaLiveMessage": ({ direction }) => `Moved ${direction}.`,
  "edge.a11yDescription.default":
    "Press Enter to select this connection, then Delete to remove it, or Escape to cancel.",
  "controls.ariaLabel": "Zoom",
  "controls.zoomIn.ariaLabel": "Zoom in",
  "controls.zoomOut.ariaLabel": "Zoom out",
  "controls.fitView.ariaLabel": "Fit everything in view",
  "handle.ariaLabel": "Connection point",
};
const edgeTypes: EdgeTypes = { route: RouteEdge };

/** How long after the last arrow-key press a station's new place is saved. */
const KEY_MOVE_SAVE_MS = 400;

/** Tidy up (or any graph update that moves several stations at once)
 *  glides them to their new places; one moved station just lands. */
const GLIDE_MS = 240;
const easeOut = (t: number) => 1 - (1 - t) ** 3;

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
  switchedOff,
  times,
  drawKey = null,
  diff,
  readOnly = false,
  label,
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
  /** Node ids drawn as switched off (an MCP server turned off). */
  switchedOff?: ReadonlySet<string>;
  /** Node id -> time the shown run spent there, in ms. */
  times?: ReadonlyMap<string, number>;
  /** An id for the run being shown: its lit route draws itself once. */
  drawKey?: string | null;
  /** Two versions being compared: what to mark on the drawing. */
  diff?: FlowOptions["diff"];
  /** A drawing to look at, not to edit: nothing moves, connects or is
   *  removed. Stations can still be reached and picked from the keyboard. */
  readOnly?: boolean;
  /** What this drawing is, for a screen reader (the builder's is its tab). */
  label?: string;
}) {
  const { resolvedTheme } = useTheme();
  const flow = useMemo(
    () =>
      toFlow(graph, issues, sourceLabels ?? {}, highlight, { switchedOff, times, drawKey, diff }),
    [graph, issues, sourceLabels, highlight, switchedOff, times, drawKey, diff],
  );
  const [keyFocus, setKeyFocus] = useState<LineFamily | null>(null);
  const view = useMemo(() => ({ keyFocus }), [keyFocus]);

  // React Flow's own copy of the nodes and edges. It reports each node's
  // measured size, drags and selection through onNodesChange / onEdgesChange,
  // and hides a node until it has a size. The canvas used to pass the graph's
  // nodes straight through with no change handler, so every graph update (a
  // delete, a save, data arriving after mount) rebuilt the nodes without a
  // size and left them invisible until a tab switch remounted the canvas.
  const [nodes, setNodes] = useState<Node[]>([]);
  const [edges, setEdges] = useState<Edge[]>([]);
  const current = useRef<Node[]>([]);
  current.current = nodes;
  const glide = useRef<number | null>(null);

  useEffect(() => {
    if (glide.current !== null) cancelAnimationFrame(glide.current);
    glide.current = null;
    const prev = current.current;
    const next = mergeFlowNodes(prev, flow.nodes, selectedId);
    const from = new Map(prev.map((n) => [n.id, n.position]));
    const moving = next.filter((n) => {
      const p = from.get(n.id);
      return p !== undefined && (p.x !== n.position.x || p.y !== n.position.y);
    });
    if (moving.length < 2 || prefersReducedMotion()) {
      setNodes(next);
      return;
    }
    // Start where they were, then move only positions each frame, so a size
    // React Flow measures meanwhile is never overwritten.
    const to = new Map(moving.map((n) => [n.id, n.position]));
    setNodes(next.map((n) => (to.has(n.id) ? { ...n, position: from.get(n.id)! } : n)));
    const start = performance.now();
    const step = (now: number) => {
      const t = Math.min(1, (now - start) / GLIDE_MS);
      const k = easeOut(t);
      setNodes((ns) =>
        ns.map((n) => {
          const a = from.get(n.id);
          const b = to.get(n.id);
          if (!a || !b) return n;
          return { ...n, position: { x: a.x + (b.x - a.x) * k, y: a.y + (b.y - a.y) * k } };
        }),
      );
      glide.current = t < 1 ? requestAnimationFrame(step) : null;
    };
    glide.current = requestAnimationFrame(step);
    return () => {
      if (glide.current !== null) cancelAnimationFrame(glide.current);
      glide.current = null;
      // Interrupted: land everything where the graph says.
      setNodes((ns) => ns.map((n) => (to.has(n.id) ? { ...n, position: to.get(n.id)! } : n)));
    };
  }, [flow.nodes, selectedId]);
  useEffect(() => {
    setEdges((prev) => mergeFlowEdges(prev, flow.edges));
  }, [flow.edges]);

  // Arrow keys move the selected station (React Flow does that), but a move
  // made that way never reached the graph: React Flow reports a drag's end
  // and not a key's, so the station jumped back at the next save. Its new
  // place is saved shortly after the last key press.
  const byKeyboard = useRef(false);
  const keyMoves = useRef(new Map<string, { x: number; y: number }>());
  const keyMoveTimer = useRef<number | null>(null);
  const moved = useRef(onNodeMoved);
  moved.current = onNodeMoved;
  const flushKeyMoves = useCallback(() => {
    keyMoveTimer.current = null;
    for (const [id, position] of keyMoves.current) moved.current(id, position);
    keyMoves.current.clear();
  }, []);
  useEffect(
    () => () => {
      if (keyMoveTimer.current !== null) {
        window.clearTimeout(keyMoveTimer.current);
        flushKeyMoves();
      }
    },
    [flushKeyMoves],
  );

  // Removals are not applied here: they go through onNodesDelete /
  // onEdgesDelete to the graph, which is the source of truth, and come back
  // through the effects above.
  const onNodesChange = useCallback(
    (changes: NodeChange[]) => {
      if (byKeyboard.current && !readOnly) {
        for (const c of changes) {
          if (c.type === "position" && c.position && !c.dragging) {
            keyMoves.current.set(c.id, c.position);
          }
        }
        if (keyMoves.current.size > 0) {
          if (keyMoveTimer.current !== null) window.clearTimeout(keyMoveTimer.current);
          keyMoveTimer.current = window.setTimeout(flushKeyMoves, KEY_MOVE_SAVE_MS);
        }
      }
      setNodes((ns) =>
        applyNodeChanges(
          changes.filter((c) => c.type !== "remove"),
          ns,
        ),
      );
    },
    [readOnly, flushKeyMoves],
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

  // Kept the same function between renders: React Flow hands it to every
  // station, and a new one each time re-rendered all of them whenever
  // anything on the canvas changed (task 6.8).
  const handleNodeClick: NodeMouseHandler = useCallback((_, node) => onSelect(node.id), [onSelect]);
  // Selecting a station from the keyboard (focus, then Enter) opens its
  // settings as a click does. Only from the keyboard: React Flow also
  // selects a station when a drag starts, and a drag must not open the
  // drawer (a click opens it through onNodeClick, which a drag never fires).
  // Only a single selection: an empty one also happens while a just-added
  // node is still being saved.
  const handleSelectionChange: OnSelectionChangeFunc = useCallback(
    ({ nodes: picked }) => {
      if (byKeyboard.current && picked.length === 1) onSelect(picked[0].id);
    },
    [onSelect],
  );

  // A drawing to look at is kept whole in view: when it is given a new
  // graph (another pair of versions) and when its box changes size. The
  // builder's canvas is left where the person put it.
  const instance = useRef<ReactFlowInstance | null>(null);
  const box = useRef<HTMLDivElement>(null);
  const nodeCount = nodes.length;
  useEffect(() => {
    if (!readOnly) return;
    // A timer, not an animation frame: a frame never comes while the page
    // is in a background tab, and the drawing would stay as it first fell.
    let timer = 0;
    const fit = () => {
      window.clearTimeout(timer);
      // After React Flow has measured what it was just given.
      timer = window.setTimeout(() => void instance.current?.fitView({ padding: 0.08 }), 60);
    };
    fit();
    const el = box.current;
    if (!el || typeof ResizeObserver !== "function") return () => window.clearTimeout(timer);
    const watcher = new ResizeObserver(fit);
    watcher.observe(el);
    return () => {
      window.clearTimeout(timer);
      watcher.disconnect();
    };
  }, [readOnly, graph, nodeCount]);

  return (
    <CanvasViewContext.Provider value={view}>
      <div
        ref={box}
        className="studio-canvas relative h-full w-full"
        onPointerDownCapture={() => {
          byKeyboard.current = false;
        }}
        onKeyDownCapture={() => {
          byKeyboard.current = true;
        }}
      >
        <ReactFlow
          nodes={nodes}
          edges={edges}
          onNodesChange={onNodesChange}
          onEdgesChange={onEdgesChange}
          nodeTypes={nodeTypes}
          edgeTypes={edgeTypes}
          onNodeClick={handleNodeClick}
          onSelectionChange={handleSelectionChange}
          onPaneClick={() => onSelect(null)}
          onNodeDragStop={(_, node) => onNodeMoved(node.id, node.position)}
          aria-label={label}
          nodesDraggable={!readOnly}
          // Only the pair is saved: which of the Agent's inputs a line uses
          // is worked out again from positions every render.
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
          connectionLineComponent={RouteConnectionLine}
          defaultEdgeOptions={{ interactionWidth: 16 }}
          snapToGrid
          snapGrid={[GRID, GRID]}
          colorMode={resolvedTheme === "dark" ? "dark" : "light"}
          fitView
          fitViewOptions={{ padding: 0.2 }}
          // React Flow stops zooming out at 0.5, which cannot show a pipeline
          // of a hundred stations, or a comparison in a narrow column: "fit
          // everything in view" then cut both ends off.
          minZoom={0.1}
          proOptions={{ hideAttribution: true }}
          onInit={(flowInstance) => {
            instance.current = flowInstance;
          }}
          ariaLabelConfig={ARIA_LABELS}
          nodesConnectable={Boolean(onConnect) && !readOnly}
          deleteKeyCode={onDeleteNodes && !readOnly ? ["Backspace", "Delete"] : null}
        >
          <Background gap={GRID} size={1.25} />
          <Controls showInteractive={false} position="bottom-left" />
        </ReactFlow>
        {/* Next to the zoom controls (React Flow's panel sits 15px in and
            is 34px wide). Capped to the canvas height, opening upward. */}
        <div className="pointer-events-none absolute top-3 bottom-[15px] left-16 z-10 flex">
          <CanvasKey graph={graph} onFocusFamily={setKeyFocus} />
        </div>
      </div>
    </CanvasViewContext.Provider>
  );
}
