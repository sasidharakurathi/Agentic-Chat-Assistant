"use client";

import { useCallback, useEffect, useState } from "react";

import { BudgetLimitsForm } from "@/components/usage/Budgets";
import { Alert } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Loading } from "@/components/ui/loading";
import { SectionHeading } from "@/components/ui/section-heading";
import { ApiError, budgets as budgetsApi, type PeopleBudgets as People } from "@/lib/api";
import { formatUsd } from "@/lib/format";
import { DEFAULT_ROLES, limitWords, usedShare } from "@/lib/people-limits";

const text = (n: number | null | undefined) => (n === null || n === undefined ? "" : String(n));

/** Limits for each person (Phase 8.9), for the admins: a default for each
 *  Employee and one for each builder, admin and the owner, and one person's
 *  own in place of their role's. Reaching it stops only that person. Also
 *  everyone's spend today and this month: only admins see it. */
export function PeopleBudgets({ orgId }: { orgId: string }) {
  const [data, setData] = useState<People | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [editing, setEditing] = useState<string | null>(null);

  const load = useCallback(() => {
    budgetsApi
      .people(orgId)
      .then(setData)
      .catch((err) =>
        setError(
          err instanceof ApiError
            ? err.message
            : "Couldn't reach the server. Check your connection, then reload.",
        ),
      );
  }, [orgId]);

  useEffect(() => load(), [load]);

  if (error) return <Alert>{error}</Alert>;
  if (!data) return <Loading what="the limits for each person" />;

  return (
    <section aria-labelledby="usage-people" className="flex flex-col gap-6">
      <SectionHeading
        id="usage-people"
        title="Limits for each person"
        description="Everything with a person's name on it counts: their conversations, previews and AI help. Reaching their limit stops only them, and they are told without money at 80% and 100%. Only admins see this."
      />
      <div className="grid max-w-200 gap-6 md:grid-cols-2">
        {DEFAULT_ROLES.map(({ role, label }) => {
          const d = data.defaults.find((x) => x.role === role);
          return (
            <div key={role} className="flex flex-col gap-2">
              <h3 className="text-h4 font-semibold">{label}</h3>
              <BudgetLimitsForm
                initial={{ daily: text(d?.daily_usd), monthly: text(d?.monthly_usd) }}
                onSave={async (limits) =>
                  setData(await budgetsApi.setPersonDefault(orgId, role, limits))
                }
              />
            </div>
          );
        })}
      </div>
      <div className="max-w-full overflow-x-auto" tabIndex={0} role="region" aria-label="People">
        <table className="text-body w-full min-w-160 border-collapse text-left">
          <thead>
            <tr className="border-border border-b">
              <th className="py-1.5 pr-4 font-semibold">Person</th>
              <th className="py-1.5 pr-4 font-semibold">Today</th>
              <th className="py-1.5 pr-4 font-semibold">This month</th>
              <th className="py-1.5 pr-4 font-semibold">
                <span className="sr-only">Change</span>
              </th>
            </tr>
          </thead>
          <tbody>
            {data.people.map((p) => {
              const today = usedShare(p.spent_today_usd, p.daily_usd);
              const month = usedShare(p.spent_month_usd, p.monthly_usd);
              return (
                <tr key={p.user_id} className="border-border border-b align-top">
                  <td className="py-2 pr-4">
                    <span className="block">{p.name || p.email}</span>
                    <span className="text-small text-muted-foreground">{p.role}</span>
                  </td>
                  <td className="num py-2 pr-4">
                    {formatUsd(p.spent_today_usd)} of {limitWords(p.daily_usd, p.own_daily)}
                    {today !== null && today >= 80 && (
                      <span className="text-small text-muted-foreground block">{today}% used</span>
                    )}
                  </td>
                  <td className="num py-2 pr-4">
                    {formatUsd(p.spent_month_usd)} of {limitWords(p.monthly_usd, p.own_monthly)}
                    {month !== null && month >= 80 && (
                      <span className="text-small text-muted-foreground block">{month}% used</span>
                    )}
                  </td>
                  <td className="py-2 pr-4">
                    {editing === p.user_id ? (
                      <div className="flex min-w-64 flex-col gap-2">
                        <BudgetLimitsForm
                          initial={{
                            daily: p.own_daily ? text(p.daily_usd) : "",
                            monthly: p.own_monthly ? text(p.monthly_usd) : "",
                          }}
                          onSave={async (limits) => {
                            setData(await budgetsApi.setPersonOverride(orgId, p.user_id, limits));
                            setEditing(null);
                          }}
                        />
                        <p className="text-small text-muted-foreground">
                          Empty: back to their role&apos;s limit.
                        </p>
                        <Button
                          variant="ghost"
                          size="sm"
                          className="self-start"
                          onClick={() => setEditing(null)}
                        >
                          Cancel
                        </Button>
                      </div>
                    ) : (
                      <Button variant="ghost" size="sm" onClick={() => setEditing(p.user_id)}>
                        Their own limit
                      </Button>
                    )}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </section>
  );
}
