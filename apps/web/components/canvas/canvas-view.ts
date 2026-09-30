"use client";

import { createContext, useContext } from "react";

import type { LineFamily } from "@/components/ui/line-bullet";

/** View state the canvas shares with its stations and lines without going
 *  through the graph: the family the Key is pointing at, which dims every
 *  other family to `line-unlit`. */
export const CanvasViewContext = createContext<{ keyFocus: LineFamily | null }>({
  keyFocus: null,
});

export const useCanvasView = () => useContext(CanvasViewContext);

/** Line colour per station type, as stroke classes. Written out in full so
 *  Tailwind sees them. A line takes its source station's colour. */
export const NODE_STROKE: Record<string, string> = {
  input: "stroke-node-main",
  guardrail: "stroke-node-main",
  router: "stroke-node-main",
  agent: "stroke-node-main",
  output: "stroke-node-main",
  knowledge_base: "stroke-node-kb",
  data_source: "stroke-node-datasource",
  memory: "stroke-node-memory",
  tool: "stroke-node-tool",
  database: "stroke-node-database",
  mcp_server: "stroke-node-mcp",
  subagent: "stroke-node-subagent",
};

export const strokeFor = (type: string) => NODE_STROKE[type] ?? "stroke-node-main";

/** Whether the user asked to skip animation. */
export function prefersReducedMotion(): boolean {
  return (
    typeof window !== "undefined" &&
    typeof window.matchMedia === "function" &&
    window.matchMedia("(prefers-reduced-motion: reduce)").matches
  );
}
