"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

import { Badge } from "@/components/ui/badge";
import { buttonVariants } from "@/components/ui/button";
import { ApiError, conversations, runs, type RunTrace as Trace, type TraceStep } from "@/lib/api";
import { formatUsd } from "@/lib/format";
import { approvalLine, canvasHref, formatMs, nestSteps, stepTitle } from "@/lib/run-trace";
import { cn } from "@/lib/utils";

/** One answer's run, step by step (task 5.9): what it cost and how it
 *  ended, then every tool call in order with its timing, the approval it
 *  waited on, a subagent's notes and what guardrails did. "Show on canvas"
 *  lights up the nodes it went through. */
export function RunTrace({
  conversationId,
  messageId,
  onClose,
}: {
  conversationId: string;
  messageId: string;
  onClose: () => void;
}) {
  const [trace, setTrace] = useState<Trace | null | undefined>(undefined);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let live = true;
    void (async () => {
      try {
        const page = await conversations.runs(conversationId, messageId);
        const run = page.items[0];
        const t = run ? await runs.trace(conversationId, run.id) : null;
        if (live) setTrace(t);
      } catch (err) {
        if (live) setError(err instanceof ApiError ? err.message : "Could not load the run");
      }
    })();
    return () => {
      live = false;
    };
  }, [conversationId, messageId]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  return (
    <aside
      aria-label="Run details"
      className="border-border bg-background fixed inset-y-0 right-0 z-40 flex w-[min(30rem,100vw)] flex-col border-l shadow-xl"
    >
      <header className="border-border flex items-center justify-between border-b px-4 py-3">
        <h2 className="text-sm font-semibold">Run details</h2>
        <button
          onClick={onClose}
          aria-label="Close"
          className="text-muted-foreground hover:text-foreground text-lg leading-none"
        >
          &times;
        </button>
      </header>
      <div className="flex-1 overflow-auto px-4 py-3 text-xs">
        {error && <p className="text-destructive">{error}</p>}
        {!error && trace === undefined && <p className="text-muted-foreground">Loading…</p>}
        {!error && trace === null && (
          <p className="text-muted-foreground">No run was recorded for this message.</p>
        )}
        {trace && <TraceBody trace={trace} conversationId={conversationId} />}
      </div>
    </aside>
  );
}

function TraceBody({ trace, conversationId }: { trace: Trace; conversationId: string }) {
  const timed = trace.timeline.some((s) => s.started_ms != null);
  return (
    <div className="flex flex-col gap-4">
      <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-0.5">
        <dt className="text-muted-foreground">Status</dt>
        <dd>
          {trace.status}
          {trace.error ? ` — ${trace.error}` : ""}
          {trace.stop_reason && trace.stop_reason !== "end_turn"
            ? ` (stopped: ${trace.stop_reason.replace(/_/g, " ")})`
            : ""}
        </dd>
        <dt className="text-muted-foreground">Version</dt>
        <dd>{trace.version_number != null ? `v${trace.version_number}` : "draft"}</dd>
        <dt className="text-muted-foreground">Model</dt>
        <dd>
          {trace.model ?? "—"}
          {trace.effort ? ` · ${trace.effort}` : ""}
          {trace.driver ? ` · ${trace.driver} driver` : ""}
        </dd>
        {trace.route && (
          <>
            <dt className="text-muted-foreground">Routed</dt>
            <dd>
              {trace.route}, so {trace.effort ?? "the agent's"} effort
            </dd>
          </>
        )}
        {trace.fallback_model && (
          <>
            <dt className="text-muted-foreground">Answered by</dt>
            <dd>{trace.fallback_model} (the fallback model)</dd>
          </>
        )}
        <dt className="text-muted-foreground">Tokens</dt>
        <dd>
          {trace.tokens_in.toLocaleString()} in · {trace.tokens_out.toLocaleString()} out ·{" "}
          {trace.num_turns} model {trace.num_turns === 1 ? "turn" : "turns"}
        </dd>
        <dt className="text-muted-foreground">Cost</dt>
        <dd>{formatUsd(trace.cost_usd)}</dd>
        <dt className="text-muted-foreground">Duration</dt>
        <dd>{trace.duration_ms != null ? formatMs(trace.duration_ms) : "—"}</dd>
        <dt className="text-muted-foreground">Trace</dt>
        <dd className="font-mono break-all select-all">{trace.trace_id ?? "—"}</dd>
      </dl>

      <div className="flex items-center justify-between gap-2">
        <h3 className="text-xs font-semibold tracking-wide uppercase">
          Steps ({trace.timeline.length})
        </h3>
        <Link
          href={canvasHref(trace.assistant_id, conversationId, trace.id)}
          className={buttonVariants({ size: "sm", variant: "outline" })}
        >
          Show on canvas
        </Link>
      </div>
      {trace.timeline.length === 0 ? (
        <p className="text-muted-foreground">
          The answer came straight from the model: no tools, no guardrail notes.
        </p>
      ) : (
        <ol className="flex flex-col gap-2">
          {nestSteps(trace.timeline).map(({ step, depth }, i) => (
            <li key={step.id ?? `g${i}`} style={{ marginLeft: depth * 16 }}>
              <StepCard step={step} timed={timed} />
            </li>
          ))}
        </ol>
      )}
      {trace.graph === "draft" && (
        // The draft may have changed since this run; a published version's
        // own snapshot is used when a version answered.
        <p className="text-muted-foreground">Nodes are matched against the current draft.</p>
      )}
    </div>
  );
}

function StepCard({ step, timed }: { step: TraceStep; timed: boolean }) {
  const failed = step.status && step.status !== "success";
  return (
    <div
      className={cn(
        "rounded-md border px-2.5 py-2",
        step.kind === "guardrail" ? "border-warning bg-warning/5" : "border-border",
      )}
    >
      <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
        {timed && step.started_ms != null && (
          <span className="text-muted-foreground font-mono tabular-nums">
            +{formatMs(step.started_ms)}
          </span>
        )}
        <span className="font-medium">{stepTitle(step)}</span>
        {step.duration_ms != null && (
          <span className="text-muted-foreground">{formatMs(step.duration_ms)}</span>
        )}
        {failed && <Badge variant="destructive">{step.status}</Badge>}
        {step.permission && step.permission !== "auto" && (
          <Badge variant={step.permission === "approved" ? "success" : "muted"}>
            {step.permission}
          </Badge>
        )}
      </div>
      {step.approval && <p className="mt-1">{approvalLine(step.approval)}</p>}
      {step.guardrail && <p className="mt-1">{step.guardrail.detail}</p>}
      {step.subagent_text && (
        <p className="text-muted-foreground mt-1 whitespace-pre-wrap">{step.subagent_text}</p>
      )}
      {step.kind === "tool" && (
        <details className="mt-1">
          <summary className="text-muted-foreground cursor-pointer">Input and output</summary>
          <pre className="bg-muted mt-1 max-h-48 overflow-auto rounded p-2 text-[11px] whitespace-pre-wrap">
            {JSON.stringify(step.input, null, 2)}
          </pre>
          {step.output && (
            <pre className="bg-muted mt-1 max-h-48 overflow-auto rounded p-2 text-[11px] whitespace-pre-wrap">
              {step.output}
            </pre>
          )}
        </details>
      )}
    </div>
  );
}
