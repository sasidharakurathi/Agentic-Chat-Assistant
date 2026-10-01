"use client";

import {
  EdgeLabelRenderer,
  Position,
  getSmoothStepPath,
  type ConnectionLineComponentProps,
  type Edge,
  type EdgeProps,
} from "@xyflow/react";
import { TriangleAlert } from "lucide-react";
import { memo, useEffect, useRef } from "react";

import { prefersReducedMotion, strokeFor, useCanvasView } from "@/components/canvas/canvas-view";
import type { RouteEdgeData, StationData } from "@/components/canvas/graph-sync";
import { Lamp } from "@/components/ui/lamp";
import { cn } from "@/lib/utils";

export type RouteEdgeType = Edge<RouteEdgeData, "route">;

/** How far before a line's end the safety marker sits. */
const MARKER_OFFSET = 28;

function beforeEnd(x: number, y: number, side: Position) {
  if (side === Position.Top) return { x, y: y - MARKER_OFFSET };
  if (side === Position.Bottom) return { x, y: y + MARKER_OFFSET };
  if (side === Position.Right) return { x: x + MARKER_OFFSET, y };
  return { x: x - MARKER_OFFSET, y };
}

/** A line on the canvas (docs/DESIGN.md section 6). Orthogonal with 16px
 *  corners, butt caps, no arrowheads. The main line is 5px ink; capability
 *  lines are 3px in their source station's colour. Dashed means "won't
 *  run" and nothing else: an invalid edge is a 3px dashed red line with a
 *  label plate. A 7px underlay shows hover (and the 7px marker band shows
 *  selection). While a run is shown, lit lines keep their colour and draw
 *  themselves once; unlit lines go grey. When two versions are compared, a
 *  line the newer one added sits on a success band, and a line it removed
 *  is dashed red: it is, exactly, a line that no longer runs. */
export const RouteEdge = memo(function RouteEdge({
  id,
  sourceX,
  sourceY,
  targetX,
  targetY,
  sourcePosition,
  targetPosition,
  data,
  selected,
  interactionWidth = 16,
}: EdgeProps<RouteEdgeType>) {
  const { keyFocus } = useCanvasView();
  const [path, labelX, labelY] = getSmoothStepPath({
    sourceX,
    sourceY,
    sourcePosition,
    targetX,
    targetY,
    targetPosition,
    borderRadius: 16,
  });
  const main = data?.line === "main";
  const problem = data?.problem;
  const removed = data?.diff === "removed";
  const added = data?.diff === "added";
  const unlit =
    data?.trace === "dim" || (keyFocus !== null && data !== undefined && keyFocus !== data.family);
  const colour =
    problem || removed
      ? "stroke-destructive"
      : unlit
        ? "stroke-line-unlit"
        : strokeFor(data?.sourceType ?? "");

  // The one signature moment: opening a run draws its lit route once.
  const line = useRef<SVGPathElement>(null);
  const drawn = useRef<string | null>(null);
  const lit = data?.trace === "lit";
  const drawKey = data?.drawKey ?? null;
  useEffect(() => {
    if (!lit || !drawKey || drawn.current === drawKey) return;
    drawn.current = drawKey;
    const el = line.current;
    if (!el || typeof el.animate !== "function" || prefersReducedMotion()) return;
    const length = el.getTotalLength();
    el.animate(
      [
        { strokeDasharray: `${length} ${length}`, strokeDashoffset: `${length}` },
        { strokeDasharray: `${length} ${length}`, strokeDashoffset: "0" },
      ],
      { duration: 600, easing: "ease-out" },
    );
  }, [lit, drawKey]);

  const marker = data?.writes && !problem ? beforeEnd(targetX, targetY, targetPosition) : null;

  return (
    <>
      <g className="group">
        {/* Keyboard focus on the edge (React Flow hides its outline): an ink
            ring with a paper gap inside it, so it reads even around the ink
            main line. Then hover and selection, under the line itself. */}
        <path
          d={path}
          fill="none"
          className={cn(
            "stroke-ring opacity-0 in-focus-visible:opacity-100",
            main ? "stroke-15" : "stroke-13",
          )}
        />
        <path
          d={path}
          fill="none"
          className={cn(
            "stroke-background opacity-0 in-focus-visible:opacity-100",
            main ? "stroke-11" : "stroke-9",
          )}
        />
        <path
          d={path}
          fill="none"
          className={cn(
            "transition-opacity duration-120 ease-out",
            main ? "stroke-9" : "stroke-7",
            selected
              ? "stroke-marker opacity-100"
              : added
                ? "stroke-success opacity-100"
                : "stroke-line-unlit opacity-0 group-hover:opacity-100",
          )}
        />
        <path
          ref={line}
          id={id}
          d={path}
          fill="none"
          className={cn(
            "transition-[stroke] duration-120 ease-out",
            colour,
            main && !problem && !removed ? "stroke-5" : "stroke-3",
            (problem || removed) && "[stroke-dasharray:6_4]",
          )}
        />
        {interactionWidth > 0 && (
          <path
            d={path}
            fill="none"
            strokeOpacity={0}
            strokeWidth={interactionWidth}
            className="react-flow__edge-interaction"
          />
        )}
      </g>
      {(problem || marker) && (
        <EdgeLabelRenderer>
          {problem && (
            <div
              className="nodrag nopan border-destructive bg-card text-small text-foreground pointer-events-auto absolute flex max-w-[220px] items-start gap-1.5 rounded-sm border px-2 py-1"
              style={{ transform: `translate(-50%, -50%) translate(${labelX}px, ${labelY}px)` }}
            >
              <Lamp tone="destructive" className="mt-1" />
              <span>{problem}</span>
            </div>
          )}
          {marker && (
            <span
              role="img"
              aria-label="Can change data"
              tabIndex={0}
              className="nodrag nopan group/marker bg-warning text-warning-foreground focus-visible:ring-ring focus-visible:ring-offset-background pointer-events-auto absolute flex size-[18px] items-center justify-center rounded-full focus-visible:ring-2 focus-visible:ring-offset-2 focus-visible:outline-none"
              style={{ transform: `translate(-50%, -50%) translate(${marker.x}px, ${marker.y}px)` }}
            >
              <TriangleAlert aria-hidden className="size-3" strokeWidth={2.5} />
              <span
                aria-hidden
                className="border-border bg-card text-small text-foreground shadow-float pointer-events-none absolute bottom-full left-1/2 mb-1.5 hidden -translate-x-1/2 rounded-md border px-2 py-1 whitespace-nowrap group-hover/marker:block group-focus-visible/marker:block"
              >
                Can change data
              </span>
            </span>
          )}
        </EdgeLabelRenderer>
      )}
    </>
  );
});

/** The line drawn while dragging a new connection: 3px in the source
 *  station's colour. The target handle turns success or destructive over a
 *  valid or invalid target (globals.css `.studio-handle`). */
export function RouteConnectionLine({
  fromX,
  fromY,
  toX,
  toY,
  fromPosition,
  toPosition,
  fromNode,
}: ConnectionLineComponentProps) {
  const type = (fromNode?.data as StationData | undefined)?.node?.type ?? "";
  const [path] = getSmoothStepPath({
    sourceX: fromX,
    sourceY: fromY,
    sourcePosition: fromPosition,
    targetX: toX,
    targetY: toY,
    targetPosition: toPosition,
    borderRadius: 16,
  });
  return <path d={path} fill="none" className={cn("stroke-3", strokeFor(type))} />;
}
