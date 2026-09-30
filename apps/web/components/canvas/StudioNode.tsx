"use client";

import { Handle, Position, useStore, type Node, type NodeProps } from "@xyflow/react";

import { useCanvasView } from "@/components/canvas/canvas-view";
import type { StationData } from "@/components/canvas/graph-sync";
import { problemSummary, stationDetail, stationName } from "@/components/canvas/station";
import { Lamp } from "@/components/ui/lamp";
import { LineBullet, nodeFamily } from "@/components/ui/line-bullet";
import { formatMs } from "@/lib/run-trace";
import { cn } from "@/lib/utils";

/** Below this zoom only the bullet and the name are drawn. */
const DETAIL_ZOOM = 0.7;

/** A station on the canvas (docs/DESIGN.md section 6). A 52px plate, so
 *  handles on one row line up and the main line stays straight: a 24px
 *  line bullet (its stop number on the main line, its glyph elsewhere), the
 *  name, and one line of plain detail. The Agent is the interchange: wider,
 *  with an ink outline and three inputs (left for the main line, top and
 *  bottom for capability lines). Input and Output are termini. */
export function StudioNode({ data, selected }: NodeProps<Node<StationData>>) {
  const { node, issues, sourceLabel, trace, stop, off = false, time } = data;
  const { keyFocus } = useCanvasView();
  const showDetail = useStore((s) => s.transform[2] >= DETAIL_ZOOM);
  const t = node.type;
  const errors = issues?.errors ?? [];
  const warnings = issues?.warnings ?? [];
  const problem = errors[0] ?? warnings[0];
  const agent = t === "agent";
  const unlit = trace === "dim" || (keyFocus !== null && keyFocus !== nodeFamily(t));

  // The first problem replaces the detail, in its lamp's colour, so the
  // message is on the canvas and not only in a tooltip. Then the time a
  // shown run spent here, then the station's own detail.
  const detail = problem
    ? problem
    : time !== undefined
      ? formatMs(time)
      : off && t !== "memory"
        ? "Switched off"
        : stationDetail(node, sourceLabel);
  const name = stationName(node, sourceLabel);
  const tooltip = [...errors, ...warnings].join("\n") || undefined;

  return (
    <div
      title={tooltip}
      className={cn(
        "bg-card relative flex h-[52px] items-center gap-2.5 rounded-md px-3 transition-colors duration-120 ease-out",
        // React Flow hides the focus outline on its node wrapper; draw one here.
        "in-focus-visible:outline-ring in-focus-visible:outline-2 in-focus-visible:outline-offset-4 in-focus-visible:outline-solid",
        agent
          ? "border-node-main max-w-66 min-w-54 border-[2.5px]"
          : "border-station-border max-w-60 min-w-44 border-[1.5px]",
        selected && !agent && "border-foreground",
        // "You are here": a marker band outside an ink edge.
        selected && "ring-marker ring-4",
        unlit && !selected && "border-line-unlit",
      )}
    >
      {(t === "input" || t === "output") && (
        <span
          aria-hidden
          className={cn(
            "absolute -inset-y-[1.5px] w-1.5",
            t === "input" ? "-left-[1.5px] rounded-l-md" : "-right-[1.5px] rounded-r-md",
            unlit ? "bg-line-unlit" : "bg-node-main",
          )}
        />
      )}

      {agent ? (
        <>
          <Handle id="left" type="target" position={Position.Left} className="studio-handle" />
          <Handle id="top" type="target" position={Position.Top} className="studio-handle" />
          <Handle id="bottom" type="target" position={Position.Bottom} className="studio-handle" />
        </>
      ) : (
        t !== "input" && <Handle type="target" position={Position.Left} className="studio-handle" />
      )}

      <LineBullet type={t} size={24} number={stop} hollow={off} unlit={unlit} decorative />
      <div className="flex min-w-0 flex-col">
        <span
          className={cn(
            "font-condensed text-h4 truncate leading-[18px] font-semibold",
            (off || unlit) && "text-muted-foreground",
          )}
        >
          {stop !== undefined && <span className="sr-only">{`${stop}. `}</span>}
          {name}
        </span>
        {showDetail && detail && (
          <span
            className={cn(
              "text-small truncate leading-4",
              problem
                ? errors.length > 0
                  ? "text-destructive"
                  : "text-warning"
                : "text-muted-foreground",
              !problem && time !== undefined && "num",
            )}
          >
            {detail}
          </span>
        )}
      </div>

      {(errors.length > 0 || warnings.length > 0) && (
        <Lamp
          tone={errors.length > 0 ? "destructive" : "warning"}
          count={errors.length || warnings.length}
          label={problemSummary(errors.length, warnings.length)}
          className="absolute -top-2.5 -right-2.5"
        />
      )}

      {t !== "output" && (
        <Handle type="source" position={Position.Right} className="studio-handle" />
      )}
    </div>
  );
}
