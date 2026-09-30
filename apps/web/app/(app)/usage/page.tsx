"use client";

import { useCallback, useEffect, useState } from "react";

import { BudgetBar, BudgetLimitsForm } from "@/components/usage/Budgets";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Tabs } from "@/components/ui/tabs";
import { ApiError, budgets as budgetsApi, usage, type Budgets, type UsageRow } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { limitsOf, rangeStart, type Range } from "@/lib/budgets";
import { formatCount, formatUsd } from "@/lib/format";

const RANGES: { value: Range; label: string }[] = [
  { value: "today", label: "Today" },
  { value: "month", label: "This month" },
  { value: "30d", label: "Last 30 days" },
];

/** What the org has spent, where, and against which limits (task 5.7). */
export default function UsagePage() {
  const { activeOrgId } = useAuth();
  const [range, setRange] = useState<Range>("month");
  const [budgetList, setBudgetList] = useState<Budgets | null>(null);
  const [byAssistant, setByAssistant] = useState<UsageRow[]>([]);
  const [byModel, setByModel] = useState<UsageRow[]>([]);
  const [topConversations, setTopConversations] = useState<UsageRow[]>([]);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    if (!activeOrgId) return;
    setError(null);
    const from = rangeStart(range);
    try {
      const [b, a, m, c] = await Promise.all([
        budgetsApi.org(activeOrgId),
        usage.rollup(activeOrgId, { group_by: "assistant", from }),
        usage.rollup(activeOrgId, { group_by: "model", from }),
        usage.rollup(activeOrgId, { group_by: "conversation", from, limit: 10 }),
      ]);
      setBudgetList(b);
      setByAssistant(a.rows);
      setByModel(m.rows);
      setTopConversations(c.rows);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not load usage");
    }
  }, [activeOrgId, range]);

  useEffect(() => {
    void load();
  }, [load]);

  const total = byModel.reduce((sum, r) => sum + Number(r.cost_usd), 0);
  const orgBudgets = budgetList?.budgets.filter((b) => b.scope === "org") ?? [];
  const assistantBudgets = budgetList?.budgets.filter((b) => b.scope === "assistant") ?? [];

  return (
    <div className="mx-auto flex max-w-4xl flex-col gap-6 px-6 py-10">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="font-serif text-2xl font-semibold tracking-tight">Usage</h1>
          <p className="text-muted-foreground mt-1 text-sm">
            Spend across every assistant: chats, AI helpers, summaries and ingestion.
          </p>
        </div>
        <Tabs label="Period" value={range} onChange={setRange} options={RANGES} />
      </div>
      {error && (
        <p className="text-destructive text-sm" role="alert">
          {error}
        </p>
      )}

      <Card>
        <CardHeader>
          <CardTitle className="text-base">Budgets</CardTitle>
          <CardDescription>
            At 80% chats show a warning; at 100% new turns and paid AI helpers stop until the budget
            resets.
          </CardDescription>
        </CardHeader>
        <CardContent className="flex flex-col gap-5">
          {orgBudgets.length === 0 && (
            <p className="text-muted-foreground text-sm">The organisation has no spend limit.</p>
          )}
          {orgBudgets.map((b) => (
            <BudgetBar key={`${b.scope}-${b.period}`} budget={b} />
          ))}
          {budgetList?.can_edit && activeOrgId && (
            <BudgetLimitsForm
              initial={limitsOf(budgetList.budgets, null)}
              onSave={async (limits) => setBudgetList(await budgetsApi.setOrg(activeOrgId, limits))}
            />
          )}
          {assistantBudgets.length > 0 && (
            <div className="border-border flex flex-col gap-4 border-t pt-4">
              <p className="text-sm font-medium">Assistants with their own limits</p>
              {assistantBudgets.map((b) => (
                <BudgetBar key={`${b.assistant_id}-${b.period}`} budget={b} />
              ))}
              <p className="text-muted-foreground text-xs">
                Set these on each assistant: Build › Panels › Budget.
              </p>
            </div>
          )}
        </CardContent>
      </Card>

      <div className="grid gap-6 md:grid-cols-2">
        <SpendTable
          title="By assistant"
          rows={byAssistant}
          total={total}
          unnamed="Deleted assistants, or no assistant (setup helpers)"
        />
        <SpendTable title="By model" rows={byModel} total={total} unnamed="Other" />
      </div>
      <SpendTable
        title="Top conversations"
        rows={topConversations}
        total={total}
        unnamed="Deleted, or outside a conversation (ingestion, helpers)"
      />
    </div>
  );
}

function SpendTable({
  title,
  rows,
  total,
  unnamed,
}: {
  title: string;
  rows: UsageRow[];
  total: number;
  unnamed: string;
}) {
  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">{title}</CardTitle>
      </CardHeader>
      <CardContent>
        {rows.length === 0 ? (
          <p className="text-muted-foreground text-sm">Nothing spent in this period.</p>
        ) : (
          <table className="w-full text-sm">
            <thead className="text-muted-foreground text-xs">
              <tr>
                <th className="pb-2 text-left font-normal">Name</th>
                <th className="pb-2 text-right font-normal">Tokens</th>
                <th className="pb-2 text-right font-normal">Spend</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => {
                const cost = Number(r.cost_usd);
                const share = total > 0 ? Math.round((cost / total) * 100) : 0;
                return (
                  <tr key={r.group_key ?? "none"} className="border-border border-t">
                    <td className="py-1.5 pr-2">
                      <div className="truncate">
                        {r.label ?? unnamed}
                        {r.detail && <span className="text-muted-foreground"> · {r.detail}</span>}
                      </div>
                      <div className="bg-muted mt-1 h-1 rounded-full">
                        <div
                          className="bg-primary h-full rounded-full"
                          style={{ width: `${share}%` }}
                        />
                      </div>
                    </td>
                    <td className="text-muted-foreground py-1.5 text-right tabular-nums">
                      {formatCount(r.tokens_in + r.tokens_out)}
                    </td>
                    <td className="py-1.5 text-right tabular-nums">{formatUsd(cost)}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        )}
      </CardContent>
    </Card>
  );
}
