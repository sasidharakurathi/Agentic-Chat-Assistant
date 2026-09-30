"use client";

import { useEffect, useId, useState } from "react";

import { Alert } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { ApiError, type BudgetLimits, type BudgetStatus } from "@/lib/api";
import { budgetPercent, budgetTitle, budgetTone, parseLimit, resetsLabel } from "@/lib/budgets";
import { failureMessage, formatUsd } from "@/lib/format";
import { cn } from "@/lib/utils";

/** What the state means for the people chatting. */
function stateNote(state: BudgetStatus["state"], pct: number): string {
  if (state === "exceeded") return "Used up. New messages and paid AI help stop until it resets.";
  if (state === "warning") return `${pct}% used. Chats show a warning.`;
  return `${pct}% used`;
}

/** One budget (docs/DESIGN.md section 7): an 8px meter filled green, ochre
 *  from 80% and red at 100%, a 1px ink tick where the warning starts, and
 *  the amounts in words. */
export function BudgetBar({ budget }: { budget: BudgetStatus }) {
  const pct = budgetPercent(budget);
  const title = budgetTitle(budget);
  const readout = `${formatUsd(budget.spent_usd)} of ${formatUsd(budget.limit_usd)}`;
  return (
    <div className="flex flex-col gap-2">
      <div className="flex flex-wrap items-baseline justify-between gap-x-3 gap-y-0.5">
        <span className="font-medium">{title}</span>
        <span className="num">{readout}</span>
      </div>
      <div
        className="bg-muted relative h-2 rounded-full"
        role="meter"
        aria-label={title}
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={pct}
        aria-valuetext={`${readout}, ${pct}% used`}
      >
        <div
          className={cn("h-full rounded-full", budgetTone(budget.state))}
          style={{ width: `${pct}%` }}
        />
        {/* Where the warning starts. */}
        <div aria-hidden className="bg-foreground absolute -top-0.5 -bottom-0.5 left-[80%] w-px" />
      </div>
      <div className="text-small text-muted-foreground flex flex-wrap justify-between gap-x-3 gap-y-0.5">
        <span className="num">{stateNote(budget.state, pct)}</span>
        <span>{resetsLabel(budget)}</span>
      </div>
    </div>
  );
}

/** The day and month limits of one scope, in a plate. Empty means no
 *  limit. */
export function BudgetLimitsForm({
  initial,
  onSave,
}: {
  initial: { daily: string; monthly: string };
  onSave: (limits: BudgetLimits) => Promise<void>;
}) {
  const ids = useId();
  const [daily, setDaily] = useState(initial.daily);
  const [monthly, setMonthly] = useState(initial.monthly);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);

  useEffect(() => {
    setDaily(initial.daily);
    setMonthly(initial.monthly);
  }, [initial.daily, initial.monthly]);

  const d = parseLimit(daily);
  const m = parseLimit(monthly);
  const invalid = d === undefined || m === undefined;
  const unchanged = daily.trim() === initial.daily && monthly.trim() === initial.monthly;
  const hintId = `${ids}-hint`;

  async function save(e: React.FormEvent) {
    e.preventDefault();
    if (invalid) return;
    setBusy(true);
    setError(null);
    setSaved(false);
    try {
      await onSave({ daily_usd: d, monthly_usd: m });
      setSaved(true);
    } catch (err) {
      setError(
        failureMessage(
          "Couldn't save the limits.",
          err instanceof ApiError ? err.message : null,
          "Check the amounts, then try again.",
        ),
      );
    } finally {
      setBusy(false);
    }
  }

  return (
    <form onSubmit={save} className="border-border bg-card rounded-lg border p-4 sm:p-6">
      <fieldset className="flex min-w-0 flex-col gap-4">
        <legend className="text-h4 mb-4 font-semibold">Set limits</legend>
        <div className="grid gap-4 sm:grid-cols-2">
          <div className="flex min-w-0 flex-col gap-1.5">
            <Label htmlFor={`${ids}-day`}>Daily limit (USD)</Label>
            <Input
              id={`${ids}-day`}
              inputMode="decimal"
              placeholder="No limit"
              value={daily}
              aria-invalid={d === undefined || undefined}
              aria-describedby={hintId}
              onChange={(e) => {
                setDaily(e.target.value);
                setSaved(false);
              }}
            />
          </div>
          <div className="flex min-w-0 flex-col gap-1.5">
            <Label htmlFor={`${ids}-month`}>Monthly limit (USD)</Label>
            <Input
              id={`${ids}-month`}
              inputMode="decimal"
              placeholder="No limit"
              value={monthly}
              aria-invalid={m === undefined || undefined}
              aria-describedby={hintId}
              onChange={(e) => {
                setMonthly(e.target.value);
                setSaved(false);
              }}
            />
          </div>
        </div>
        <p
          id={hintId}
          className={cn(
            "text-small max-w-[60ch]",
            invalid ? "text-destructive" : "text-muted-foreground",
          )}
        >
          {invalid
            ? "Enter an amount of at least $0.01, or leave it empty for no limit."
            : "Leave a limit empty for none. Days and months run on UTC."}
        </p>
        {error && <Alert>{error}</Alert>}
        <div className="flex items-center gap-3">
          <Button type="submit" size="sm" disabled={busy || invalid || unchanged}>
            {busy ? "Saving…" : "Save limits"}
          </Button>
          <span role="status" className="text-small text-muted-foreground">
            {saved ? "Saved" : ""}
          </span>
        </div>
      </fieldset>
    </form>
  );
}
