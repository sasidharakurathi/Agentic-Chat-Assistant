"use client";

import { useEffect, useId, useState } from "react";

import { Alert } from "@/components/ui/alert";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Loading } from "@/components/ui/loading";
import { SectionHeading } from "@/components/ui/section-heading";
import { ApiError, assistants, type SuiteScore, type VersionEvidence } from "@/lib/api";
import { formatUsd } from "@/lib/format";

const DEFAULT_PER_MONTH = 500;

const percent = (rate: number | null) => (rate == null ? "–" : `${Math.round(rate * 100)}%`);

/** What to approve on (Phase 10.4), shown beside a submitted version: each
 *  eval suite's latest run on it, whether anything was written without
 *  approval, and what it might cost a month. All from runs that already
 *  happened; nothing here runs or spends. */
export function ApprovalEvidence({ assistantId, number }: { assistantId: string; number: number }) {
  const ids = useId();
  const [perMonth, setPerMonth] = useState(String(DEFAULT_PER_MONTH));
  const [evidence, setEvidence] = useState<VersionEvidence | null>(null);
  const [error, setError] = useState<string | null>(null);
  const volume = Number(perMonth);
  const validVolume = Number.isInteger(volume) && volume >= 1 && volume <= 10_000_000;

  useEffect(() => {
    if (!validVolume) return;
    let live = true;
    // Typed numbers settle before asking again.
    const timer = setTimeout(() => {
      assistants
        .evidence(assistantId, number, volume)
        .then((e) => {
          if (!live) return;
          setEvidence(e);
          setError(null);
        })
        .catch((err) => {
          if (!live) return;
          setError(
            `Couldn't load the evidence for version ${number}. ${
              err instanceof ApiError ? err.message : "The server couldn't be reached."
            } Reload the page to try again.`,
          );
        });
    }, 300);
    return () => {
      live = false;
      clearTimeout(timer);
    };
  }, [assistantId, number, volume, validVolume]);

  return (
    <section
      aria-labelledby={`${ids}-heading`}
      className="border-border mb-6 flex flex-col gap-4 rounded-lg border p-4 sm:p-6"
    >
      <SectionHeading
        level={3}
        id={`${ids}-heading`}
        title={`Evidence for version ${number}`}
        description="From the eval runs on this version. Run the suites from the Evals tab, choosing this version, before you decide."
      />
      {error && <Alert>{error}</Alert>}
      {!evidence && !error && <Loading what="the evidence" rows={2} />}
      {evidence && (
        <>
          <Scorecard evidence={evidence} />
          <Safety safety={evidence.safety} />
          <div className="flex flex-col gap-1.5">
            <h4 className="font-medium">Cost forecast</h4>
            <div className="flex flex-wrap items-end gap-3">
              <div className="flex flex-col gap-1.5">
                <Label htmlFor={`${ids}-volume`}>Expected conversations a month</Label>
                <Input
                  id={`${ids}-volume`}
                  type="number"
                  min={1}
                  max={10_000_000}
                  className="w-40"
                  aria-invalid={!validVolume || undefined}
                  aria-describedby={`${ids}-cost`}
                  value={perMonth}
                  onChange={(e) => setPerMonth(e.target.value)}
                />
              </div>
              {evidence.cost.mid_usd != null && (
                <p className="num pb-2 text-sm" aria-live="polite">
                  About {formatUsd(evidence.cost.low_usd ?? 0)} to{" "}
                  {formatUsd(evidence.cost.high_usd ?? 0)} a month
                </p>
              )}
            </div>
            <p id={`${ids}-cost`} className="text-small text-muted-foreground max-w-[60ch]">
              {!validVolume
                ? "Enter a whole number of conversations, at least 1."
                : evidence.cost.avg_cost_usd != null
                  ? `${evidence.cost.note} Average per eval conversation: ${formatUsd(
                      evidence.cost.avg_cost_usd,
                    )}.`
                  : evidence.cost.note}
            </p>
          </div>
          {evidence.notes.length > 0 && (
            <ul className="text-small text-muted-foreground list-disc pl-5">
              {evidence.notes.map((n) => (
                <li key={n}>{n}</li>
              ))}
            </ul>
          )}
        </>
      )}
    </section>
  );
}

function Scorecard({ evidence }: { evidence: VersionEvidence }) {
  if (evidence.scorecard.length === 0) return null;
  return (
    <div className="flex flex-col gap-1.5">
      <h4 className="font-medium">Eval scorecard</h4>
      <ul className="border-border flex flex-col border-t">
        {evidence.scorecard.map((s) => (
          <SuiteRow key={s.suite_id} score={s} comparedWith={evidence.compared_with} />
        ))}
      </ul>
    </div>
  );
}

function SuiteRow({ score, comparedWith }: { score: SuiteScore; comparedWith: number | null }) {
  if (score.run_id == null) {
    return (
      <li className="border-border border-b py-2 text-sm">
        <span className="font-medium">{score.suite_name}</span>
        <span className="text-muted-foreground"> · not run on this version</span>
      </li>
    );
  }
  return (
    <li className="border-border flex flex-col gap-1 border-b py-2 text-sm">
      <div className="flex flex-wrap items-baseline justify-between gap-x-3">
        <span className="font-medium">{score.suite_name}</span>
        <span className="num">
          {score.passed} of {score.cases} passed ({percent(score.pass_rate)})
          {score.errors > 0 && `, ${score.errors} errored`}
        </span>
      </div>
      {score.previous_pass_rate != null && comparedWith != null && (
        <p className="text-small text-muted-foreground num">
          Version {comparedWith}: {percent(score.previous_pass_rate)}
        </p>
      )}
      {score.ran_on === "draft" && (
        <p className="text-small text-muted-foreground">
          Run on the draft after this version was submitted.
        </p>
      )}
      {score.regressions.length > 0 && (
        <Alert tone="warning" title="Worse than the version it replaces">
          <ul className="list-disc pl-5">
            {score.regressions.map((r) => (
              <li key={r}>{r.charAt(0).toUpperCase() + r.slice(1)}.</li>
            ))}
          </ul>
        </Alert>
      )}
    </li>
  );
}

function Safety({ safety }: { safety: VersionEvidence["safety"] }) {
  const calls = `${safety.checked_calls} tool call${safety.checked_calls === 1 ? "" : "s"}`;
  return (
    <div className="flex flex-col gap-1.5">
      <h4 className="font-medium">Safety</h4>
      {safety.ok ? (
        <Alert tone="success">
          No write ran without approval ({calls} checked
          {safety.approval_requests > 0
            ? `; ${safety.approval_requests} asked for approval and were held`
            : ""}
          ).
        </Alert>
      ) : (
        <Alert title={`${safety.writes_without_approval} write(s) ran without approval`}>
          Writes that ran without anyone approving them, out of {calls}:{" "}
          {safety.flagged.map((f) => f.tool.split("__").pop()).join(", ")}. Check the runs in the
          Evals tab before approving.
        </Alert>
      )}
      {safety.approval_cases > 0 && (
        <p className="text-small text-muted-foreground num">
          {safety.approval_cases_passed} of {safety.approval_cases} cases that must wait for
          approval did.
        </p>
      )}
    </div>
  );
}
