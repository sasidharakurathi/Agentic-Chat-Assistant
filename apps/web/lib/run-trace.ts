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
