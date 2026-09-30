"use client";

import { useEffect, useId, useState } from "react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { ApiError, type BudgetLimits, type BudgetStatus } from "@/lib/api";
import { budgetPercent, budgetTitle, budgetTone, parseLimit, resetsLabel } from "@/lib/budgets";
import { formatUsd } from "@/lib/format";
import { cn } from "@/lib/utils";

/** One budget: how much of it is gone, amber from 80%, red at 100%. */
export function BudgetBar({ budget }: { budget: BudgetStatus }) {
  const pct = budgetPercent(budget);
  return (
    <div className="flex flex-col gap-1">
      <div className="flex items-baseline justify-between gap-3 text-sm">
        <span className="font-medium">{budgetTitle(budget)}</span>
        <span className="text-muted-foreground tabular-nums">
          {formatUsd(budget.spent_usd)} of {formatUsd(budget.limit_usd)}
        </span>
      </div>
      <div
        className="bg-muted h-2 overflow-hidden rounded-full"
        role="meter"
        aria-label={budgetTitle(budget)}
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={pct}
      >
        <div
          className={cn("h-full rounded-full", budgetTone(budget.state))}
          style={{ width: `${pct}%` }}
        />
      </div>
      <div className="text-muted-foreground flex justify-between text-xs">
        <span>
          {budget.state === "exceeded"
            ? "Used up: new turns and paid helpers are stopped."
            : budget.state === "warning"
              ? `${pct}% used: chats show a warning.`
              : `${pct}% used`}
        </span>
        <span>{resetsLabel(budget)}</span>
      </div>
    </div>
  );
}

/** The day and month limits of one scope. Empty means no limit. */
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
      setError(err instanceof ApiError ? err.message : "Could not save the limits");
    } finally {
      setBusy(false);
    }
  }

  return (
    <form onSubmit={save} className="flex flex-col gap-2">
      <div className="grid grid-cols-2 gap-3">
        <div className="flex flex-col gap-1.5">
          <Label htmlFor={`${ids}-day`}>Daily limit (USD)</Label>
          <Input
            id={`${ids}-day`}
            inputMode="decimal"
            placeholder="none"
            value={daily}
            onChange={(e) => {
              setDaily(e.target.value);
              setSaved(false);
            }}
          />
        </div>
        <div className="flex flex-col gap-1.5">
          <Label htmlFor={`${ids}-month`}>Monthly limit (USD)</Label>
          <Input
            id={`${ids}-month`}
            inputMode="decimal"
            placeholder="none"
            value={monthly}
            onChange={(e) => {
              setMonthly(e.target.value);
              setSaved(false);
            }}
          />
        </div>
      </div>
      <p className="text-muted-foreground text-xs">
        {invalid
          ? "A limit is an amount of at least $0.01, or empty for none."
          : "Days and months are UTC. Empty means no limit."}
      </p>
      {error && (
        <p className="text-destructive text-xs" role="alert">
          {error}
        </p>
      )}
      <div className="flex items-center gap-2">
        <Button type="submit" size="sm" disabled={busy || invalid || unchanged}>
          {busy ? "Saving…" : "Save limits"}
        </Button>
        {saved && <span className="text-muted-foreground text-xs">Saved.</span>}
      </div>
    </form>
  );
}
