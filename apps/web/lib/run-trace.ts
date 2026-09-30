import type { TraceStep } from "@/lib/api";
import { subagentLabel, toolLabel } from "@/lib/tool-label";

/** How a step reads in the timeline. */
export function stepTitle(
  step: Pick<TraceStep, "kind" | "name"> & Partial<Pick<TraceStep, "input" | "guardrail">>,
): string {
  if (step.kind === "guardrail") {
    return `Guardrail: ${step.guardrail?.check ?? "check"}`;
  }
  return (
    subagentLabel(step.name, (step.input ?? undefined) as Record<string, unknown> | undefined) ??
    toolLabel(step.name)
  );
}

/** 320 -> "320 ms", 1234 -> "1.2 s", 65000 -> "1 m 5 s". */
export function formatMs(ms: number): string {
  if (ms < 1000) return `${Math.round(ms)} ms`;
  if (ms < 60_000) return `${(ms / 1000).toFixed(1)} s`;
  const m = Math.floor(ms / 60_000);
  return `${m} m ${Math.round((ms % 60_000) / 1000)} s`;
}

/** Steps in order, each with how deep it sits: a subagent's own calls go
 *  under the delegation that ran them. */
export function nestSteps<T extends Pick<TraceStep, "id" | "parent_id">>(
  steps: T[],
): { step: T; depth: number }[] {
  const ids = new Set(steps.map((s) => s.id).filter(Boolean));
  const top = steps.filter((s) => !s.parent_id || !ids.has(s.parent_id));
  const out: { step: T; depth: number }[] = [];
  const add = (s: T, depth: number) => {
    out.push({ step: s, depth });
    for (const child of steps.filter((c) => c.parent_id && c.parent_id === s.id)) {
      add(child, depth + 1);
    }
  };
  top.forEach((s) => add(s, 0));
  return out;
}

/** "Approved by Sam after 12 s", "Denied by Sam after 3 s", "No answer:
 *  declined after 5 m 0 s", "Waiting for someone to answer". */
export function approvalLine(a: NonNullable<TraceStep["approval"]>): string {
  const after = a.wait_ms != null ? ` after ${formatMs(a.wait_ms)}` : "";
  if (a.status === "approved") return `Approved by ${a.decided_by ?? "someone"}${after}`;
  if (a.status === "denied") return `Denied by ${a.decided_by ?? "someone"}${after}`;
  if (a.status === "expired") return `No answer: declined${after}`;
  return "Waiting for someone to answer";
}

/** The canvas, lit up with one run: `/assistants/<id>/build?run=<conv>.<run>`. */
export function canvasHref(assistantId: string, conversationId: string, runId: string): string {
  return `/assistants/${assistantId}/build?run=${conversationId}.${runId}`;
}

export function parseRunParam(
  value: string | null,
): { conversationId: string; runId: string } | null {
  const m = /^([0-9a-f-]{36})\.([0-9a-f-]{36})$/i.exec(value ?? "");
  return m ? { conversationId: m[1], runId: m[2] } : null;
}

/** A run's outcome as a word and a lamp tone, never the raw enum. */
export function runStatus(status: string): {
  label: string;
  tone: "success" | "warning" | "destructive" | "muted";
} {
  switch (status) {
    case "ok":
      return { label: "Finished", tone: "success" };
    case "error":
      return { label: "Failed", tone: "destructive" };
    case "aborted":
      return { label: "Stopped", tone: "warning" };
    case "refused":
      return { label: "Declined by the model", tone: "muted" };
    default:
      return {
        label: status ? status[0].toUpperCase() + status.slice(1) : "Unknown",
        tone: "muted",
      };
  }
}

/** Why the model stopped, when it wasn't simply done. Null for a normal end. */
export function stopReasonText(reason: string | null | undefined): string | null {
  if (!reason || reason === "end_turn" || reason === "stop_sequence") return null;
  switch (reason) {
    case "max_tokens":
      return "It reached the answer length limit.";
    case "refusal":
      return "The model declined to answer.";
    case "pause_turn":
      return "The model paused partway.";
    case "tool_use":
      return "It stopped while waiting on a tool.";
    default:
      return `It stopped early (${reason.replace(/_/g, " ")}).`;
  }
}

/** The main line in route order. */
export const MAIN_TYPES = ["input", "guardrail", "router", "agent", "output"] as const;
export type MainType = (typeof MAIN_TYPES)[number];

export type RouteStop = {
  type: MainType;
  /** The stop's number on the canvas; absent when the graph is unknown or
   *  the main line doesn't reach this station. */
  number?: number;
  /** Whether this run passed through it. */
  lit: boolean;
};

/** The main-line stops a run's route strip shows.
 *
 *  With the assistant's graph (node id -> type), the stops are the main-line
 *  types it has, lit when the run touched them (the router counts as
 *  touched when the run was routed). Numbers come from `stopNumbers` (type
 *  -> the number the canvas gives that station from the real wiring, see
 *  `mainLineStops`), so the strip and the canvas agree; a station the line
 *  doesn't reach is missing from it and shows no number. Without
 *  `stopNumbers` the present types are counted in route order. Without the
 *  graph, only what the run itself proves is shown, unnumbered: Input,
 *  Guardrails if any guardrail step ran, Router if it was routed, Agent and
 *  Output. */
export function mainStops({
  graphTypes,
  stopNumbers,
  touched,
  routed,
  guardrailSteps,
}: {
  graphTypes: Map<string, string> | null;
  stopNumbers?: Map<string, number>;
  touched: string[];
  routed: boolean;
  guardrailSteps: boolean;
}): RouteStop[] {
  if (graphTypes && graphTypes.size > 0) {
    const present = new Set(graphTypes.values());
    const lit = new Set(touched.map((id) => graphTypes.get(id)).filter(Boolean));
    if (routed) lit.add("router");
    if (guardrailSteps) lit.add("guardrail");
    const stops = MAIN_TYPES.filter((t) => present.has(t));
    // Ids from another snapshot of the graph: nothing matches, so say only
    // what the run proves rather than dimming every stop.
    const anyLit = stops.some((t) => lit.has(t));
    return stops.map((type, i) => ({
      type,
      number: stopNumbers ? stopNumbers.get(type) : i + 1,
      lit: !anyLit || lit.has(type) || type === "input" || type === "output",
    }));
  }
  return MAIN_TYPES.filter(
    (t) => (t !== "guardrail" || guardrailSteps) && (t !== "router" || routed),
  ).map((type) => ({ type, lit: true }));
}
