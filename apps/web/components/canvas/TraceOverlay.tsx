"use client";

import type { RunTrace } from "@/lib/api";
import { formatUsd } from "@/lib/format";
import { formatMs, nestSteps, stepTitle } from "@/lib/run-trace";
import { cn } from "@/lib/utils";

/** A run shown on the canvas (task 5.9): its steps, over the graph. All of
 *  them lit by default; choosing one lights just the nodes it touched. */
export function TraceOverlay({
  trace,
  selected,
  onSelect,
  onClose,
}: {
  trace: RunTrace;
  /** Index into `trace.timeline`, or null for the whole run. */
  selected: number | null;
  onSelect: (index: number | null) => void;
  onClose: () => void;
}) {
  const steps = nestSteps(trace.timeline.map((s, index) => ({ ...s, index })));
  return (
    <section
      aria-label="Run on the canvas"
      className="border-border bg-card absolute top-14 right-3 z-10 flex max-h-[70%] w-72 flex-col rounded-lg border text-xs shadow-lg"
    >
      <header className="border-border flex items-start justify-between gap-2 border-b px-3 py-2">
        <div>
          <p className="font-semibold">Run on the canvas</p>
          <p className="text-muted-foreground">
            {new Date(trace.created_at).toLocaleString()} · {formatUsd(trace.cost_usd)}
            {trace.duration_ms != null && ` · ${formatMs(trace.duration_ms)}`}
          </p>
        </div>
        <button
          onClick={onClose}
          aria-label="Stop showing the run"
          className="text-muted-foreground hover:text-foreground text-base leading-none"
        >
          &times;
        </button>
      </header>
      <ol className="flex-1 overflow-auto p-1.5">
        <li>
          <button
            onClick={() => onSelect(null)}
            aria-pressed={selected === null}
            className={cn(
              "w-full rounded px-2 py-1 text-left",
              selected === null ? "bg-muted font-medium" : "hover:bg-muted/60",
            )}
          >
            The whole run ({trace.nodes.length} nodes)
          </button>
        </li>
        {steps.map(({ step, depth }) => (
          <li key={step.index} style={{ paddingLeft: depth * 12 }}>
            <button
              onClick={() => onSelect(step.index)}
              aria-pressed={selected === step.index}
              className={cn(
                "flex w-full items-baseline justify-between gap-2 rounded px-2 py-1 text-left",
                selected === step.index ? "bg-muted font-medium" : "hover:bg-muted/60",
              )}
            >
              <span className="truncate">{stepTitle(step)}</span>
              <span className="text-muted-foreground shrink-0">
                {step.nodes.length === 0
                  ? "not on canvas"
                  : step.duration_ms != null
                    ? formatMs(step.duration_ms)
                    : ""}
              </span>
            </button>
          </li>
        ))}
      </ol>
      {trace.graph === "draft" && trace.version_number != null && (
        <p className="text-muted-foreground border-border border-t px-3 py-2">
          Shown on the draft: version {trace.version_number} wasn&apos;t found.
        </p>
      )}
    </section>
  );
}
