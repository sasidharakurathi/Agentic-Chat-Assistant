"use client";

import { ChevronRight, Waypoints, X } from "lucide-react";
import Link from "next/link";
import { Fragment, useEffect, useId, useRef, useState, type ReactNode } from "react";

import { mainLineStops } from "@/components/canvas/graph-sync";
import { CodeBlock } from "@/components/chat/CodeBlock";
import { asSentence, failureText } from "@/components/chat/error-text";
import { Alert } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button, buttonVariants } from "@/components/ui/button";
import { Lamp } from "@/components/ui/lamp";
import { LineBullet, NODE_BG, NODE_TYPE_LABEL } from "@/components/ui/line-bullet";
import { Loading } from "@/components/ui/loading";
import { SectionHeading } from "@/components/ui/section-heading";
import {
  assistants,
  conversations,
  runs,
  type Graph,
  type GuardrailFinding,
  type RunTrace as Trace,
  type TraceStep,
} from "@/lib/api";
import { formatUsd } from "@/lib/format";
import { modelName } from "@/lib/subagent-models";
import { guardrailTitle } from "@/lib/message-blocks";
import {
  approvalLine,
  canvasHref,
  formatMs,
  mainStops,
  nestSteps,
  runStatus,
  stopReasonText,
  type RouteStop,
} from "@/lib/run-trace";
import { toolNodeType, toolSentence } from "@/lib/tool-label";
import { permissionText } from "@/lib/tool-tree";
import { cn } from "@/lib/utils";

/** One answer's run (docs/DESIGN.md section 7): what it cost and how it
 *  ended as labelled figures, then the Route, a vertical strip map of the
 *  stops the message passed and every step the agent took, in line colours
 *  with their times. "Show on canvas" lights up the same route on the
 *  builder.
 *
 *  A side sheet on a native modal `<dialog>`: focus moves in and stays in,
 *  Esc closes it, and the caller puts focus back on its trigger. */
export function RunTrace({
  conversationId,
  messageId,
  onClose,
}: {
  conversationId: string;
  messageId: string;
  onClose: () => void;
}) {
  const ref = useRef<HTMLDialogElement>(null);
  const titleId = useId();
  const [trace, setTrace] = useState<Trace | null | undefined>(undefined);
  const [graph, setGraph] = useState<RouteGraph | null>(null);
  const [error, setError] = useState<string | null>(null);

  // Opened once on mount. Unmounting removes the dialog from the page,
  // which also takes it out of the top layer; no close() here, or a
  // development double-mount would close the sheet it just opened.
  useEffect(() => {
    const el = ref.current;
    if (el && !el.open) el.showModal();
  }, []);

  useEffect(() => {
    let live = true;
    void (async () => {
      try {
        const page = await conversations.runs(conversationId, messageId);
        const run = page.items[0];
        const t = run ? await runs.trace(conversationId, run.id) : null;
        if (!live) return;
        setTrace(t);
        if (!t) return;
        // The graph names the stops and numbers them the way the canvas
        // does. Without it the strip shows only what the run proves.
        try {
          const a = await assistants.get(t.assistant_id);
          if (live) setGraph(routeGraph(a.draft_graph));
        } catch {
          /* the route still draws without it */
        }
      } catch (err) {
        if (live) {
          setError(failureText("Couldn't load this run.", err, "Close this panel and try again."));
        }
      }
    })();
    return () => {
      live = false;
    };
  }, [conversationId, messageId]);

  return (
    <dialog
      ref={ref}
      aria-label="Run details"
      onClose={onClose}
      // Esc: close through our own state rather than natively, so the
      // caller always hears about it (some browsers skip the close event).
      onCancel={(e) => {
        e.preventDefault();
        onClose();
      }}
      onClick={(e) => {
        // A click on the scrim lands on the <dialog> itself.
        if (e.target === e.currentTarget) onClose();
      }}
      // Sheets slide (220ms, the same curve as the left sheets' sheet-in),
      // here in from the right edge. A transition from @starting-style, so
      // it runs as the dialog opens; the global reduced-motion rule zeroes it.
      className="bg-background text-foreground border-border shadow-float backdrop:bg-scrim fixed inset-y-0 right-0 left-auto m-0 h-dvh max-h-none w-[min(30rem,100vw)] max-w-none translate-x-0 border-l p-0 transition-[translate] duration-220 ease-[cubic-bezier(0.2,0,0,1)] motion-reduce:transition-none starting:open:translate-x-full"
    >
      <div className="flex h-full flex-col">
        <header className="border-border flex h-14 shrink-0 items-center justify-between gap-3 border-b px-5">
          <h2 id={titleId} className="text-h3 font-semibold">
            Run details
          </h2>
          <Button variant="ghost" size="icon" aria-label="Close" onClick={onClose}>
            <X aria-hidden />
          </Button>
        </header>
        <div className="relative flex-1 overflow-y-auto px-5 py-5">
          {error && <Alert>{error}</Alert>}
          {!error && trace === undefined && <Loading what="run details" />}
          {!error && trace === null && (
            <p className="text-muted-foreground">No run was recorded for this message.</p>
          )}
          {trace && <TraceBody trace={trace} conversationId={conversationId} graph={graph} />}
        </div>
      </div>
    </dialog>
  );
}

/** What the route strip needs from the assistant's graph: each node's type,
 *  and each main-line type's stop number from the real wiring, exactly as
 *  the canvas numbers its stations (`mainLineStops`). A main-line station
 *  the line doesn't reach has no number there, so it has none here. */
type RouteGraph = { types: Map<string, string>; numbers: Map<string, number> };

function routeGraph(g: Graph): RouteGraph {
  const types = new Map(g.nodes.map((n) => [n.id, n.type]));
  const numbers = new Map<string, number>();
  for (const [id, n] of mainLineStops(g)) {
    const type = types.get(id);
    if (type && !numbers.has(type)) numbers.set(type, n);
  }
  return { types, numbers };
}

function sentenceCase(word: string): string {
  return word ? word[0].toUpperCase() + word.slice(1) : word;
}

function Figure({ label, children, wide }: { label: string; children: ReactNode; wide?: boolean }) {
  return (
    <div className={cn("flex min-w-0 flex-col gap-0.5", wide && "col-span-full")}>
      <dt className="text-small text-muted-foreground">{label}</dt>
      <dd className="min-w-0 font-medium break-words">{children}</dd>
    </div>
  );
}

function TraceBody({
  trace,
  conversationId,
  graph,
}: {
  trace: Trace;
  conversationId: string;
  graph: RouteGraph | null;
}) {
  const routeId = useId();
  const status = runStatus(trace.status);
  const stopped = stopReasonText(trace.stop_reason);
  const turns = trace.num_turns;

  return (
    <div className="flex flex-col gap-8">
      <div className="flex flex-col gap-4">
        <dl className="grid grid-cols-2 gap-x-6 gap-y-4 sm:grid-cols-3">
          <Figure label="Status">
            <Badge variant={status.tone}>{status.label}</Badge>
          </Figure>
          <Figure label="Cost">
            <span className="num">{formatUsd(trace.cost_usd)}</span>
          </Figure>
          <Figure label="Time">
            <span className="num">
              {trace.duration_ms != null ? formatMs(trace.duration_ms) : "Not recorded"}
            </span>
          </Figure>
          <Figure label="Model">{trace.model ? modelName(trace.model) : "Not recorded"}</Figure>
          {trace.effort && <Figure label="Effort">{sentenceCase(trace.effort)}</Figure>}
          <Figure label="Version">
            {trace.version_number != null ? (
              <span className="num">Version {trace.version_number}</span>
            ) : (
              "Draft"
            )}
          </Figure>
          <Figure label="Tokens">
            <span className="num">
              {trace.tokens_in.toLocaleString()} in, {trace.tokens_out.toLocaleString()} out
            </span>
          </Figure>
          <Figure label="Model calls">
            <span className="num">{turns.toLocaleString()}</span>
          </Figure>
          {trace.fallback_model && (
            <Figure label="Answered by" wide>
              {modelName(trace.fallback_model)}, the fallback model
            </Figure>
          )}
        </dl>
        {(trace.error || stopped) && (
          <Alert tone={trace.error ? "destructive" : "warning"} role="note">
            {[trace.error ? asSentence(trace.error) : null, stopped].filter(Boolean).join(" ")}
          </Alert>
        )}
      </div>

      <section aria-labelledby={routeId} className="flex flex-col gap-4">
        <SectionHeading
          level={3}
          id={routeId}
          title="Route"
          actions={
            <Link
              href={canvasHref(trace.assistant_id, conversationId, trace.id)}
              className={buttonVariants({ size: "sm", variant: "outline" })}
            >
              <Waypoints aria-hidden />
              Show on canvas
            </Link>
          }
        />
        <RouteStrip trace={trace} graph={graph} />
        {trace.graph === "draft" && (
          // The draft may have changed since this run; a published version's
          // own snapshot is used when a version answered.
          <p className="text-small text-muted-foreground max-w-[60ch]">
            This run is matched against the current draft, which may have changed since it ran.
          </p>
        )}
      </section>

      {trace.trace_id && (
        <dl className="border-border border-t pt-4">
          <Figure label="Trace ID, for support">
            <span className="text-small font-normal break-all select-all">{trace.trace_id}</span>
          </Figure>
        </dl>
      )}
    </div>
  );
}

/** Padding and connector length per nesting depth: a subagent's own calls
 *  branch further from the main line than the delegation that ran them. */
const DEPTH_PAD = ["pl-[38px]", "pl-[58px]", "pl-[78px]"] as const;
const DEPTH_LINE = ["w-5", "w-10", "w-[60px]"] as const;

/** The vertical strip map: the ink main line with its stops, and the steps
 *  the agent took branching off it in their capability's colour. The main
 *  line draws once when the strip appears (steady under reduced motion). */
function RouteStrip({ trace, graph }: { trace: Trace; graph: RouteGraph | null }) {
  // Guardrail notes on the incoming message belong at the Guardrails stop;
  // everything else happened at the Agent, in order.
  const onMessage = trace.timeline.filter(
    (s) => s.kind === "guardrail" && s.guardrail?.where === "user_message",
  );
  const atAgent = nestSteps(trace.timeline.filter((s) => !onMessage.includes(s)));
  const stops = mainStops({
    graphTypes: graph?.types ?? null,
    stopNumbers: graph?.numbers,
    touched: trace.nodes,
    routed: Boolean(trace.route),
    guardrailSteps: trace.timeline.some((s) => s.kind === "guardrail"),
  });
  const hasGuardStop = stops.some((s) => s.type === "guardrail");

  return (
    <div className="relative">
      <span
        aria-hidden
        className="bg-node-main absolute top-3 bottom-3 left-[10px] w-1 origin-top transition-transform duration-[600ms] ease-out motion-reduce:transition-none starting:scale-y-0"
      />
      <ol aria-label="Route">
        {stops.map((stop) => (
          <Fragment key={stop.type}>
            <StopRow stop={stop} trace={trace} steps={atAgent.length} />
            {stop.type === (hasGuardStop ? "guardrail" : "input") &&
              onMessage.map((step, i) => <StepRow key={`m${i}`} step={step} depth={0} />)}
            {stop.type === "agent" &&
              atAgent.map(({ step, depth }, i) => (
                <StepRow key={step.id ?? `g${i}`} step={step} depth={depth} />
              ))}
          </Fragment>
        ))}
      </ol>
    </div>
  );
}

function stopDetail(stop: RouteStop, trace: Trace, steps: number): string {
  switch (stop.type) {
    case "input":
      return "Your message";
    case "guardrail":
      return stop.lit ? "Checked the message" : "Not used this time";
    case "router":
      return trace.route ? `Routed as ${trace.route}` : "Not used this time";
    case "agent": {
      const model = trace.fallback_model ?? trace.model;
      if (steps === 0) return "Answered straight from the model, with no tools or guardrail notes";
      const did = `Took ${steps} ${steps === 1 ? "step" : "steps"}`;
      return model ? `${did} with ${modelName(model)}` : did;
    }
    case "output":
      return "The answer";
  }
}

function StopRow({ stop, trace, steps }: { stop: RouteStop; trace: Trace; steps: number }) {
  const last = stop.type === "output";
  return (
    <li className={cn("relative flex items-start gap-3", !last && "pb-5")}>
      <LineBullet
        type={stop.type}
        size={24}
        number={stop.number}
        unlit={!stop.lit}
        decorative
        className="relative"
      />
      <div className="flex min-h-6 min-w-0 flex-1 flex-wrap items-baseline gap-x-3">
        <span
          className={cn(
            "font-condensed text-body font-semibold",
            !stop.lit && "text-muted-foreground",
          )}
        >
          {NODE_TYPE_LABEL[stop.type]}
        </span>
        <span className="text-small text-muted-foreground min-w-0 flex-1 break-words">
          {stopDetail(stop, trace, steps)}
        </span>
        {last && trace.duration_ms != null && (
          <span className="num text-small text-muted-foreground shrink-0">
            {formatMs(trace.duration_ms)}
          </span>
        )}
      </div>
    </li>
  );
}

function StepRow({ step, depth }: { step: TraceStep; depth: number }) {
  const [open, setOpen] = useState(false);
  const bodyId = useId();
  const input =
    step.input && typeof step.input === "object" ? (step.input as Record<string, unknown>) : {};
  const type = step.kind === "guardrail" ? "guardrail" : toolNodeType(step.name);
  const title =
    step.kind === "guardrail" && step.guardrail
      ? guardrailTitle({
          check: step.guardrail.check as GuardrailFinding["check"],
          where: step.guardrail.where as GuardrailFinding["where"],
        })
      : toolSentence(step.name, input);
  const failed = step.status && step.status !== "success";
  const permission =
    step.permission && step.permission !== "auto" ? permissionText(step.permission) : null;
  const d = Math.min(depth, DEPTH_PAD.length - 1);

  return (
    <li className={cn("relative pb-4", DEPTH_PAD[d])}>
      <span
        aria-hidden
        className={cn("absolute top-[9px] left-[14px] h-[3px]", DEPTH_LINE[d], NODE_BG[type])}
      />
      <div className="flex items-start gap-2.5">
        <LineBullet type={type} size={20} className="relative" />
        <div className="flex min-w-0 flex-1 flex-col gap-1">
          <div className="flex flex-wrap items-baseline gap-x-3 gap-y-0.5">
            <span className="min-w-0 flex-1 break-words">{title}</span>
            {failed && (
              <span className="text-small inline-flex shrink-0 items-center gap-1.5 self-center font-medium">
                <Lamp tone="destructive" />
                {step.status === "error" ? "Failed" : "Didn't finish"}
              </span>
            )}
            {step.duration_ms != null && (
              <span className="num text-small text-muted-foreground shrink-0">
                {formatMs(step.duration_ms)}
              </span>
            )}
          </div>
          {permission && <p className="text-small text-muted-foreground">{permission}.</p>}
          {step.approval && (
            <p className="text-small text-muted-foreground">{approvalLine(step.approval)}.</p>
          )}
          {step.guardrail?.detail && (
            <p className="text-small text-muted-foreground break-words">{step.guardrail.detail}</p>
          )}
          {step.subagent_text && (
            <p className="text-small text-muted-foreground break-words whitespace-pre-wrap">
              {step.subagent_text}
            </p>
          )}
          {step.kind === "tool" && (
            <div>
              <Button
                type="button"
                variant="ghost"
                size="sm"
                className="group text-muted-foreground hover:text-foreground -ml-3 h-7"
                aria-expanded={open}
                aria-controls={bodyId}
                onClick={() => setOpen((v) => !v)}
              >
                <ChevronRight
                  aria-hidden
                  className="transition-transform duration-120 ease-out group-aria-expanded:rotate-90 motion-reduce:transition-none"
                />
                Input and output
              </Button>
              {open && (
                <div id={bodyId} className="mt-1 flex flex-col gap-2">
                  <CodeBlock
                    size="sm"
                    label="Input"
                    code={JSON.stringify(step.input ?? {}, null, 2)}
                  />
                  {step.output && <CodeBlock size="sm" label="Output" code={step.output} />}
                </div>
              )}
            </div>
          )}
        </div>
      </div>
    </li>
  );
}
