"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";

import { Alert } from "@/components/ui/alert";
import { Button, buttonVariants } from "@/components/ui/button";
import { Loading } from "@/components/ui/loading";
import { BudgetBar, BudgetLimitsForm } from "@/components/usage/Budgets";
import { ApiError, budgets as budgetsApi, type Budgets } from "@/lib/api";
import { limitsOf } from "@/lib/budgets";
import { failureMessage } from "@/lib/format";

/** The budgets that apply to one assistant: the organization's and its
 *  own. Owners and admins set its own here; the limit for each
 *  conversation stays with the Agent settings. */
export function AssistantBudget({ assistantId }: { assistantId: string }) {
  const [data, setData] = useState<Budgets | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setError(null);
    try {
      setData(await budgetsApi.assistant(assistantId));
    } catch (err) {
      setError(
        failureMessage(
          "Couldn't load the budgets.",
          err instanceof ApiError ? err.message : null,
          "Check your connection, then try again.",
        ),
      );
    }
  }, [assistantId]);

  useEffect(() => {
    void load();
  }, [load]);

  if (error) {
    return (
      <div className="flex flex-col items-start gap-3">
        <Alert className="w-full">{error}</Alert>
        <Button size="sm" variant="outline" onClick={() => void load()}>
          Try again
        </Button>
      </div>
    );
  }
  if (!data) return <Loading what="budgets" />;

  return (
    <div className="flex flex-col gap-6">
      {data.budgets.length === 0 ? (
        <p className="text-muted-foreground">
          No daily or monthly limit applies to this assistant.
        </p>
      ) : (
        <div className="flex flex-col gap-5">
          {data.budgets.map((b) => (
            <BudgetBar key={`${b.scope}-${b.period}`} budget={b} />
          ))}
        </div>
      )}
      {data.can_edit ? (
        <BudgetLimitsForm
          initial={limitsOf(data.budgets, assistantId)}
          onSave={async (limits) => setData(await budgetsApi.setAssistant(assistantId, limits))}
        />
      ) : (
        <p className="text-small text-muted-foreground">Only owners and admins can set budgets.</p>
      )}
      <p className="text-small text-muted-foreground max-w-[60ch]">
        The organization&apos;s limits are on the{" "}
        <Link
          href="/usage"
          className={buttonVariants({ variant: "link", className: "text-small" })}
        >
          Usage page
        </Link>
        . A limit for each conversation is in the Agent settings.
      </p>
    </div>
  );
}
