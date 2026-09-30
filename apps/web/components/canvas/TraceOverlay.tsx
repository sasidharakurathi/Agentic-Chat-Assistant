"use client";

import { X } from "lucide-react";

import { Button } from "@/components/ui/button";
import { LineBullet } from "@/components/ui/line-bullet";
import type { Graph, GuardrailFinding, RunTrace, TraceStep } from "@/lib/api";
import { formatUsd } from "@/lib/format";
import { guardrailTitle } from "@/lib/message-blocks";
import { formatMs, nestSteps } from "@/lib/run-trace";
import { toolNodeType, toolSentence } from "@/lib/tool-label";
import { cn } from "@/lib/utils";

const INDENT = ["", "pl-4", "pl-8", "pl-12"];

/** A step as a plain sentence, the same words as Run details in the chat:
 *  "Ran a SQL query", "Personal data removed". */
function stepSentence(step: TraceStep): string {
  if (step.kind === "guardrail" && step.guardrail) {
    return guardrailTitle({
      check: step.guardrail.check as GuardrailFinding["check"],
      where: step.guardrail.where as GuardrailFinding["where"],
    });
  }
  const input =
    step.input && typeof step.input === "object" ? (step.input as Record<string, unknown>) : {};
  return toolSentence(step.name, input);
}

/** A run shown on the canvas (task 5.9): its steps, over the graph. All of
 *  them lit by default; choosing one lights just the stations it touched.
 *  A floating layer the page places at the top-right, under Tidy up. */
export function TraceOverlay({
  trace,
  selected,
  onSelect,
  onClose,
  graph,
  className,
}: {
  trace: RunTrace;
  /** Index into `trace.timeline`, or null for the whole run. */
  selected: number | null;
  onSelect: (index: number | null) => void;
  onClose: () => void;
  /** The canvas's graph, to show each step's line bullet. */
  graph?: Graph;
  className?: string;
}) {
  const steps = nestSteps(trace.timeline.map((s, index) => ({ ...s, index })));
  const typeOf = new Map((graph?.nodes ?? []).map((n) => [n.id, n.type]));
  const item =
    "focus-visible:ring-ring flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-left text-sm transition-colors duration-120 ease-out focus-visible:ring-2 focus-visible:outline-none";

  return (
    <section
      aria-label="Run on the canvas"
      className={cn(
        "border-border bg-card shadow-float pointer-events-auto flex min-h-0 flex-col rounded-lg border",
        className,
      )}
    >
      <header className="border-border flex items-start gap-2 border-b py-2 pr-2 pl-3">
        <div className="min-w-0 flex-1">
          <h2 className="text-h4 font-semibold">Run on the canvas</h2>
          <dl className="text-small mt-1 grid grid-cols-[auto_1fr] gap-x-3 gap-y-0.5">
            <dt className="text-muted-foreground">Started</dt>
            <dd className="num">
              {new Date(trace.created_at).toLocaleString(undefined, {
                dateStyle: "medium",
                timeStyle: "short",
              })}
            </dd>
            <dt className="text-muted-foreground">Cost</dt>
            <dd className="num">{formatUsd(trace.cost_usd)}</dd>
            {trace.duration_ms != null && (
              <>
                <dt className="text-muted-foreground">Time</dt>
                <dd className="num">{formatMs(trace.duration_ms)}</dd>
              </>
            )}
          </dl>
        </div>
        <Button
          type="button"
          variant="ghost"
          size="icon"
          onClick={onClose}
          aria-label="Stop showing the run"
          title="Stop showing the run"
        >
          <X aria-hidden />
        </Button>
      </header>
      <ol className="min-h-0 flex-1 overflow-y-auto p-1.5">
        <li>
          <button
            type="button"
            onClick={() => onSelect(null)}
            aria-pressed={selected === null}
            className={cn(item, selected === null ? "bg-muted font-medium" : "hover:bg-muted")}
          >
            <span className="flex-1">The whole run</span>
          </button>
        </li>
        {steps.map(({ step, depth }) => {
          const type = step.nodes.map((id) => typeOf.get(id)).find(Boolean);
          const offCanvas = step.nodes.length === 0;
          return (
            <li key={step.index} className={INDENT[Math.min(depth, INDENT.length - 1)]}>
              <button
                type="button"
                onClick={() => onSelect(step.index)}
                aria-pressed={selected === step.index}
                className={cn(
                  item,
                  selected === step.index ? "bg-muted font-medium" : "hover:bg-muted",
                )}
              >
                {type ? (
                  <LineBullet type={type} size={16} decorative />
                ) : (
                  <LineBullet
                    type={step.kind === "guardrail" ? "guardrail" : toolNodeType(step.name)}
                    size={16}
                    unlit
                    decorative
                  />
                )}
                <span className="min-w-0 flex-1 truncate">{stepSentence(step)}</span>
                <span className="text-small text-muted-foreground num shrink-0 text-right">
                  {offCanvas
                    ? "Not on the canvas"
                    : step.duration_ms != null
                      ? formatMs(step.duration_ms)
                      : ""}
                </span>
              </button>
            </li>
          );
        })}
      </ol>
      {trace.graph === "draft" && trace.version_number != null && (
        <p className="text-small text-muted-foreground border-border border-t px-3 py-2">
          Version {trace.version_number} is no longer available, so this run is shown on the draft.
        </p>
      )}
    </section>
  );
}
