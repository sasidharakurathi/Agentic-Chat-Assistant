"use client";

import { useCallback, useEffect, useId, useState } from "react";

import { BudgetBar, BudgetLimitsForm } from "@/components/usage/Budgets";
import { Alert } from "@/components/ui/alert";
import { Loading } from "@/components/ui/loading";
import { PageHeader } from "@/components/ui/page-header";
import { SectionHeading } from "@/components/ui/section-heading";
import { Segmented } from "@/components/ui/segmented";
import { ApiError, budgets as budgetsApi, usage, type Budgets, type UsageRow } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { limitsOf, rangeStart, type Range } from "@/lib/budgets";
import { failureMessage, formatCount, formatUsd } from "@/lib/format";

const RANGES: { value: Range; label: string }[] = [
  { value: "today", label: "Today" },
  { value: "month", label: "This month" },
  { value: "30d", label: "Last 30 days" },
];

/** What the organization has spent, where, and against which limits. */
export default function UsagePage() {
  const { activeOrgId } = useAuth();
  const [range, setRange] = useState<Range>("month");
  const [budgetList, setBudgetList] = useState<Budgets | null>(null);
  const [byAssistant, setByAssistant] = useState<UsageRow[]>([]);
  const [byModel, setByModel] = useState<UsageRow[]>([]);
  const [topConversations, setTopConversations] = useState<UsageRow[]>([]);
  const [loaded, setLoaded] = useState(false);
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
      setLoaded(true);
    } catch (err) {
      setError(
        failureMessage(
          "Couldn't load usage.",
          err instanceof ApiError ? err.message : null,
          "Check your connection, then reload the page.",
        ),
      );
    }
  }, [activeOrgId, range]);

  useEffect(() => {
    void load();
  }, [load]);

  const total = byModel.reduce((sum, r) => sum + Number(r.cost_usd), 0);
  const orgBudgets = budgetList?.budgets.filter((b) => b.scope === "org") ?? [];
  const assistantBudgets = budgetList?.budgets.filter((b) => b.scope === "assistant") ?? [];

  return (
    <div className="mx-auto w-full max-w-240 px-4 py-8 md:px-6 lg:px-8">
      <PageHeader
        title="Usage"
        description="What this organization has spent on AI: chats, writing help, conversation summaries and reading sources."
      />

      {error && <Alert className="mb-6">{error}</Alert>}

      {!loaded && !error ? (
        <Loading what="usage" rows={4} />
      ) : (
        loaded && (
          <div className="flex flex-col gap-12">
            <section aria-labelledby="usage-budgets" className="flex flex-col gap-6">
              <SectionHeading
                id="usage-budgets"
                title="Budgets"
                description="At 80% of a limit, chats show a warning. At 100%, new messages and paid AI help stop until the limit resets."
              />
              {orgBudgets.length === 0 ? (
                <p className="text-muted-foreground">This organization has no spending limit.</p>
              ) : (
                <div className="flex max-w-160 flex-col gap-5">
                  {orgBudgets.map((b) => (
                    <BudgetBar key={`${b.scope}-${b.period}`} budget={b} />
                  ))}
                </div>
              )}
              {budgetList?.can_edit && activeOrgId && (
                <div className="max-w-160">
                  <BudgetLimitsForm
                    initial={limitsOf(budgetList.budgets, null)}
                    onSave={async (limits) =>
                      setBudgetList(await budgetsApi.setOrg(activeOrgId, limits))
                    }
                  />
                </div>
              )}
              {assistantBudgets.length > 0 && (
                <div className="border-border flex max-w-160 flex-col gap-5 border-t pt-6">
                  <SectionHeading
                    level={3}
                    title="Assistants with their own limits"
                    description="Change these in each assistant's builder, under Settings."
                  />
                  {assistantBudgets.map((b) => (
                    <BudgetBar key={`${b.assistant_id}-${b.period}`} budget={b} />
                  ))}
                </div>
              )}
            </section>

            <section aria-labelledby="usage-spend" className="flex flex-col gap-8">
              <SectionHeading
                id="usage-spend"
                title="Spend"
                description="Shares are of everything spent in the period."
                actions={
                  <Segmented label="Period" value={range} onChange={setRange} options={RANGES} />
                }
              />
              <div className="grid gap-8 md:grid-cols-2">
                <SpendTable
                  title="By assistant"
                  column="Assistant"
                  rows={byAssistant}
                  total={total}
                  unnamed="Deleted assistants and setup help"
                />
                <SpendTable
                  title="By model"
                  column="Model"
                  rows={byModel}
                  total={total}
                  unnamed="Other"
                />
              </div>
              <SpendTable
                title="Top conversations"
                column="Conversation"
                rows={topConversations}
                total={total}
                unnamed="Deleted conversations and background work"
              />
            </section>
          </div>
        )
      )}
    </div>
  );
}

/** A ruled table: name with a 6px share bar, then tokens and spend as
 *  right-aligned tabular figures. */
function SpendTable({
  title,
  column,
  rows,
  total,
  unnamed,
}: {
  title: string;
  column: string;
  rows: UsageRow[];
  total: number;
  unnamed: string;
}) {
  const headingId = useId();
  return (
    <div className="flex min-w-0 flex-col gap-3">
      <h3 id={headingId} className="text-h3 font-semibold">
        {title}
      </h3>
      {rows.length === 0 ? (
        <p className="text-muted-foreground border-border border-t pt-3">
          Nothing spent in this period.
        </p>
      ) : (
        <table aria-labelledby={headingId} className="w-full table-fixed border-collapse">
          <thead className="text-label text-muted-foreground">
            <tr className="border-border border-b">
              <th scope="col" className="pb-2 text-left font-medium">
                {column}
              </th>
              <th scope="col" className="w-20 pb-2 text-right font-medium">
                Tokens
              </th>
              <th scope="col" className="w-24 pb-2 text-right font-medium">
                Spend
              </th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => {
              const cost = Number(r.cost_usd);
              const share = total > 0 ? Math.round((cost / total) * 100) : 0;
              const name = r.label ?? unnamed;
              return (
                <tr key={r.group_key ?? "none"} className="border-border border-b align-top">
                  <td className="py-2.5 pr-3">
                    <div className="truncate" title={name}>
                      {name}
                    </div>
                    {r.detail && (
                      <div className="text-small text-muted-foreground truncate" title={r.detail}>
                        {r.detail}
                      </div>
                    )}
                    <div aria-hidden className="bg-muted mt-1.5 h-1.5 rounded-full">
                      <div
                        className="bg-primary h-full rounded-full"
                        style={{ width: `${share}%` }}
                      />
                    </div>
                    <span className="sr-only">{share}% of spend</span>
                  </td>
                  <td className="num text-muted-foreground py-2.5 text-right">
                    {formatCount(r.tokens_in + r.tokens_out)}
                  </td>
                  <td className="num py-2.5 text-right">{formatUsd(cost)}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      )}
    </div>
  );
}
