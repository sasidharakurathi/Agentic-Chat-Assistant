"use client";

import { useCallback, useEffect, useState } from "react";

import { BudgetBar, BudgetLimitsForm } from "@/components/usage/Budgets";
import { ApiError, budgets as budgetsApi, type Budgets } from "@/lib/api";
import { limitsOf } from "@/lib/budgets";

/** The budgets that apply to one assistant: the org's and its own (task
 *  5.7). Admins and owners set its own here; the per-conversation cap stays
 *  in the Agent panel. */
export function AssistantBudget({ assistantId }: { assistantId: string }) {
  const [data, setData] = useState<Budgets | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      setData(await budgetsApi.assistant(assistantId));
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not load budgets");
    }
  }, [assistantId]);

  useEffect(() => {
    void load();
  }, [load]);

  if (error) {
    return (
      <p className="text-destructive text-sm" role="alert">
        {error}
      </p>
    );
  }
  if (!data) return <p className="text-muted-foreground text-sm">Loading…</p>;

  return (
    <div className="flex flex-col gap-4">
      {data.budgets.length === 0 ? (
        <p className="text-muted-foreground text-sm">
          No daily or monthly limit applies to this assistant.
        </p>
      ) : (
        data.budgets.map((b) => <BudgetBar key={`${b.scope}-${b.period}`} budget={b} />)
      )}
      {data.can_edit ? (
        <BudgetLimitsForm
          initial={limitsOf(data.budgets, assistantId)}
          onSave={async (limits) => setData(await budgetsApi.setAssistant(assistantId, limits))}
        />
      ) : (
        <p className="text-muted-foreground text-xs">Only admins and owners set budgets.</p>
      )}
      <p className="text-muted-foreground text-xs">
        The organisation&apos;s limits are on the Usage page. A cap for each conversation is in the
        Agent panel (Budget).
      </p>
    </div>
  );
}
