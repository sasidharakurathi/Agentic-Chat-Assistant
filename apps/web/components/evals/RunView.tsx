"use client";

import { ChevronRight } from "lucide-react";
import { useState } from "react";

import { Alert } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Lamp } from "@/components/ui/lamp";
import { SectionHeading } from "@/components/ui/section-heading";
import type { EvalCaseResult, EvalRunDetail } from "@/lib/api";
import {
  JUDGE_DIMENSIONS,
  checkText,
  compareRuns,
  metricsOf,
  versionLabel,
  type CaseChange,
  type CheckScore,
} from "@/lib/evals";
import { formatUsd } from "@/lib/format";
import { formatMs } from "@/lib/run-trace";
import { cn } from "@/lib/utils";

type Judged = Record<string, { score: number; rationale: string } | null | boolean | string>;
type Retrieval = {
  error?: string;
  level?: string;
  k?: number;
  recall?: number;
  reciprocal_rank?: number;
  hit?: boolean;
  unknown_labels?: string[];
};
type Scores = {
  checks?: CheckScore[];
  judge?: Judged | null;
  retrieval?: Retrieval | null;
  tools?: string[];
};

/** One run of a suite (task 6.1): its numbers, then each case with what
 *  it was asked, what it answered and why it passed or didn't. */
export function RunView({ run }: { run: EvalRunDetail }) {
  const measured = metricsOf(run).filter((m) => m.text !== null);
  const judgeErrors = Number((run.metrics as { judge_errors?: number }).judge_errors ?? 0);
  return (
    <div className="flex flex-col gap-6">
      {run.error && <Alert>{run.error}</Alert>}
      {measured.length > 0 && (
        <dl className="grid grid-cols-2 gap-x-6 gap-y-3 sm:grid-cols-4">
          {measured.map((m) => (
            <div key={m.key} className="flex flex-col">
              <dt className="text-small text-muted-foreground">{m.label}</dt>
              <dd className="num text-h3 font-semibold">{m.text}</dd>
            </div>
          ))}
          <div className="flex flex-col">
            <dt className="text-small text-muted-foreground">Cost</dt>
            <dd className="num text-h3 font-semibold">{formatUsd(run.cost_usd)}</dd>
          </div>
        </dl>
      )}
      {judgeErrors > 0 && (
        <Alert tone="warning">
          The judge couldn&apos;t grade {judgeErrors} {judgeErrors === 1 ? "answer" : "answers"}.
          Those cases were decided by their checks alone.
        </Alert>
      )}
      {run.results.length > 0 && (
        <ul className="border-border border-t">
          {run.results.map((r) => (
            <ResultRow key={r.id} result={r} />
          ))}
        </ul>
      )}
    </div>
  );
}

/** The first thing to read about a case that didn't pass. */
function reason(r: EvalCaseResult, s: Scores): string | null {
  if (r.error) return r.error;
  const failed = (s.checks ?? []).find((c) => !c.passed);
  if (failed) return checkText(failed);
  if (s.judge && s.judge.passed === false) return "The judge scored it below the pass mark.";
  return null;
}

function ResultRow({ result: r }: { result: EvalCaseResult }) {
  const [open, setOpen] = useState(false);
  const s = r.scores as Scores;
  const why = r.passed ? null : reason(r, s);
  const panel = `eval-result-${r.id}`;
  return (
    <li className="border-border border-b">
      <button
        type="button"
        aria-expanded={open}
        aria-controls={panel}
        onClick={() => setOpen(!open)}
        className="hover:bg-muted/60 focus-visible:ring-ring flex w-full items-start gap-3 rounded-sm py-2.5 text-left transition-colors duration-120 ease-out focus-visible:ring-2 focus-visible:outline-none focus-visible:ring-inset"
      >
        <ChevronRight
          aria-hidden
          className={cn(
            "text-muted-foreground mt-0.5 size-4 shrink-0 transition-transform duration-120",
            open && "rotate-90",
          )}
        />
        <Badge variant={r.passed ? "success" : "destructive"} className="w-20 shrink-0">
          {r.passed ? "Passed" : "Failed"}
        </Badge>
        <span className="min-w-0 flex-1">
          <span className="block text-sm font-medium break-words">{r.input}</span>
          {why && <span className="text-small text-muted-foreground block">{why}</span>}
        </span>
        <span className="num text-small text-muted-foreground shrink-0">
          {formatMs(r.duration_ms)}
        </span>
      </button>
      {open && (
        <div id={panel} className="flex flex-col gap-4 pb-4 pl-7">
          <Block title="Answer">
            {r.output ? (
              <p className="text-reading max-w-[68ch] break-words whitespace-pre-wrap">
                {r.output}
              </p>
            ) : (
              <p className="text-muted-foreground text-sm">The assistant gave no answer.</p>
            )}
          </Block>
          {(s.checks?.length ?? 0) > 0 && (
            <Block title="Checks">
              <ul className="flex flex-col gap-1 text-sm">
                {s.checks?.map((c, i) => (
                  <li key={i} className="flex items-start gap-2">
                    <Lamp
                      tone={c.passed ? "success" : "destructive"}
                      label={c.passed ? "Held" : "Didn't hold"}
                      className="mt-1.5"
                    />
                    <span>{checkText(c)}</span>
                  </li>
                ))}
              </ul>
            </Block>
          )}
          {s.judge && <JudgeBlock judged={s.judge} />}
          {s.retrieval && <RetrievalBlock r={s.retrieval} />}
          <p className="num text-small text-muted-foreground">
            Cost {formatUsd(r.cost_usd)}
            {(s.tools?.length ?? 0) > 0 && (
              <span className="ml-4">Tools used: {s.tools?.join(", ")}</span>
            )}
          </p>
        </div>
      )}
    </li>
  );
}

function Block({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="flex flex-col gap-1.5">
      <h4 className="text-h4 font-semibold">{title}</h4>
      {children}
    </div>
  );
}

function JudgeBlock({ judged }: { judged: Judged }) {
  if (typeof judged.error === "string") {
    return (
      <Block title="Judge">
        <p className="text-muted-foreground text-sm">{judged.error}</p>
      </Block>
    );
  }
  return (
    <Block title="Judge">
      <dl className="flex flex-col gap-2 text-sm">
        {JUDGE_DIMENSIONS.map(([key, label]) => {
          const d = judged[key];
          if (!d || typeof d !== "object") return null;
          return (
            <div key={key} className="flex flex-col">
              <dt className="font-medium">
                {label} <span className="num text-muted-foreground">{d.score} of 5</span>
              </dt>
              <dd className="text-muted-foreground max-w-[68ch]">{d.rationale}</dd>
            </div>
          );
        })}
      </dl>
    </Block>
  );
}

function RetrievalBlock({ r }: { r: Retrieval }) {
  if (r.error) {
    return (
      <Block title="Retrieval">
        <p className="text-muted-foreground text-sm">{r.error}</p>
      </Block>
    );
  }
  const rank = r.reciprocal_rank ? Math.round(1 / r.reciprocal_rank) : null;
  const what = r.level === "source" ? "source" : "passage";
  return (
    <Block title="Retrieval">
      <p className="text-sm">
        {r.hit
          ? `The first relevant ${what} came at position ${rank}. Recall in the top ${r.k}: ${Math.round((r.recall ?? 0) * 100)}%.`
          : `Nothing relevant was retrieved.`}
      </p>
      {(r.unknown_labels?.length ?? 0) > 0 && (
        <p className="text-small text-muted-foreground">
          No source is named {r.unknown_labels?.join(", ")}. Check the case against the Sources tab.
        </p>
      )}
    </Block>
  );
}

const CHANGE: Record<
  CaseChange["change"],
  { word: string; variant: "success" | "destructive" | "muted" }
> = {
  fixed: { word: "Fixed", variant: "success" },
  broke: { word: "Broke", variant: "destructive" },
  same: { word: "Same", variant: "muted" },
  new: { word: "New case", variant: "muted" },
  gone: { word: "Removed", variant: "muted" },
};

const passWord = (v: boolean | null) => (v === null ? "Not run" : v ? "Passed" : "Failed");

/** What moved between two runs: `before` is the baseline. */
export function RunCompare({ before, after }: { before: EvalRunDetail; after: EvalRunDetail }) {
  const { metrics, cases } = compareRuns(before, after);
  const shown = metrics.filter((m) => m.before !== null || m.after !== null);
  const moved = cases.filter((c) => c.change !== "same");
  return (
    <div className="flex flex-col gap-6">
      <div className="overflow-x-auto">
        <table className="w-full min-w-[480px] text-left text-sm">
          <thead className="text-small text-muted-foreground">
            <tr className="border-border border-b">
              <th scope="col" className="py-2 pr-3 font-medium">
                Measure
              </th>
              <th scope="col" className="py-2 pr-3 text-right font-medium">
                {versionLabel(before)} (before)
              </th>
              <th scope="col" className="py-2 pr-3 text-right font-medium">
                {versionLabel(after)} (after)
              </th>
              <th scope="col" className="py-2 font-medium">
                Change
              </th>
            </tr>
          </thead>
          <tbody>
            {shown.map((m) => (
              <tr key={m.key} className="border-border border-b">
                <th scope="row" className="py-2 pr-3 font-normal">
                  {m.label}
                </th>
                <td className="num py-2 pr-3 text-right">{m.before ?? "Not measured"}</td>
                <td className="num py-2 pr-3 text-right">{m.after ?? "Not measured"}</td>
                <td className="py-2">
                  {m.change === "better" && <Badge variant="success">Better</Badge>}
                  {m.change === "worse" && <Badge variant="destructive">Worse</Badge>}
                  {m.change === "same" && <Badge>Same</Badge>}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <section className="flex flex-col gap-2">
        <SectionHeading
          level={4}
          title="Cases that changed"
          description={
            moved.length === 0
              ? "Every case did the same in both runs."
              : `${moved.length} of ${cases.length} did something different.`
          }
        />
        {moved.length > 0 && (
          <ul className="border-border border-t text-sm">
            {moved.map((c, i) => (
              <li key={i} className="border-border flex items-start gap-3 border-b py-2">
                <Badge variant={CHANGE[c.change].variant} className="w-24 shrink-0">
                  {CHANGE[c.change].word}
                </Badge>
                <span className="min-w-0 flex-1 break-words">{c.input}</span>
                <span className="text-small text-muted-foreground shrink-0">
                  {passWord(c.before)}, then {passWord(c.after).toLowerCase()}
                </span>
              </li>
            ))}
          </ul>
        )}
      </section>
    </div>
  );
}
