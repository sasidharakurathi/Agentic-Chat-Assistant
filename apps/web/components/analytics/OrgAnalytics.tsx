"use client";

import { Download } from "lucide-react";
import { useEffect, useId, useState } from "react";

import { Alert } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Loading } from "@/components/ui/loading";
import { SectionHeading } from "@/components/ui/section-heading";
import { Segmented } from "@/components/ui/segmented";
import { PERIODS, percent, usd, type Period } from "@/lib/analytics";
import { ApiError, analytics, type AnalyticsFigures, type OrgAnalytics } from "@/lib/api";
import { failureMessage, formatUsd } from "@/lib/format";

/** The managers' report (Phase 10.1), on the Usage page for admins: per
 *  assistant its conversations, people, resolved rate, spend and cost per
 *  resolved conversation, with the totals, and the same as a CSV file. */
export function OrgAnalyticsReport({ orgId }: { orgId: string }) {
  const [period, setPeriod] = useState<Period>("30");
  const [data, setData] = useState<OrgAnalytics | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [downloading, setDownloading] = useState(false);
  const headingId = useId();

  useEffect(() => {
    let live = true;
    setError(null);
    analytics
      .org(orgId, Number(period))
      .then((d) => live && setData(d))
      .catch(
        (err) =>
          live &&
          setError(
            failureMessage(
              "Couldn't load the assistants report.",
              err instanceof ApiError ? err.message : null,
              "Check your connection, then reload the page.",
            ),
          ),
      );
    return () => {
      live = false;
    };
  }, [orgId, period]);

  const download = async () => {
    setDownloading(true);
    setError(null);
    try {
      const blob = await analytics.orgCsv(orgId, Number(period));
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `assistant-analytics-${new Date().toISOString().slice(0, 10)}.csv`;
      a.click();
      URL.revokeObjectURL(url);
    } catch (err) {
      setError(
        failureMessage(
          "Couldn't download the report.",
          err instanceof ApiError ? err.message : null,
          "Try again in a moment.",
        ),
      );
    } finally {
      setDownloading(false);
    }
  };

  return (
    <section aria-labelledby={headingId} className="flex flex-col gap-6">
      <SectionHeading
        id={headingId}
        title="Assistants report"
        description="How each assistant's conversations in Chat went. Resolved only counts when the person said so; the rate is of the people who said."
        actions={
          <div className="flex flex-wrap items-center gap-2">
            <Segmented
              label="Report period"
              value={period}
              onChange={setPeriod}
              options={PERIODS.map((p) => ({ value: p.value, label: p.label }))}
            />
            <Button
              type="button"
              size="sm"
              variant="outline"
              onClick={() => void download()}
              disabled={downloading || !data}
            >
              <Download aria-hidden />
              {downloading ? "Downloading…" : "Download CSV"}
            </Button>
          </div>
        }
      />
      {error && <Alert>{error}</Alert>}
      {!data && !error && <Loading what="the assistants report" rows={3} />}
      {data &&
        (data.assistants.length === 0 ? (
          <p className="text-muted-foreground border-border border-t pt-3">
            This organization has no assistants yet.
          </p>
        ) : (
          <div className="overflow-x-auto">
            <table aria-labelledby={headingId} className="w-full min-w-[640px] border-collapse">
              <thead className="text-label text-muted-foreground">
                <tr className="border-border border-b">
                  <th scope="col" className="pb-2 text-left font-medium">
                    Assistant
                  </th>
                  <th scope="col" className="w-28 pb-2 text-right font-medium">
                    Conversations
                  </th>
                  <th scope="col" className="w-20 pb-2 text-right font-medium">
                    People
                  </th>
                  <th scope="col" className="w-24 pb-2 text-right font-medium">
                    Resolved
                  </th>
                  <th scope="col" className="w-24 pb-2 text-right font-medium">
                    Spend
                  </th>
                  <th scope="col" className="w-32 pb-2 text-right font-medium">
                    Per resolved
                  </th>
                </tr>
              </thead>
              <tbody>
                {data.assistants.map((r) => (
                  <Row key={r.assistant_id} name={r.name} now={r.current} before={r.previous} />
                ))}
              </tbody>
              <tfoot>
                <Row name="Total" now={data.total} before={data.previous_total} strong />
              </tfoot>
            </table>
          </div>
        ))}
      {data && (
        <p className="text-small text-muted-foreground max-w-[60ch]">
          Under each figure: the previous {data.days} days. Previews and evals aren&apos;t counted.
        </p>
      )}
    </section>
  );
}

function Row({
  name,
  now,
  before,
  strong = false,
}: {
  name: string;
  now: AnalyticsFigures;
  before: AnalyticsFigures;
  strong?: boolean;
}) {
  const said = now.resolved + now.not_resolved;
  const cell = "num py-2.5 text-right align-top";
  const prev = "text-small text-muted-foreground block";
  return (
    <tr className={strong ? "font-semibold" : "border-border border-b"}>
      <th scope="row" className="py-2.5 pr-3 text-left align-top font-[inherit]">
        <span className="block truncate" title={name}>
          {name}
        </span>
      </th>
      <td className={cell}>
        {now.conversations}
        <span className={prev}>{before.conversations}</span>
      </td>
      <td className={cell}>
        {now.people}
        <span className={prev}>{before.people}</span>
      </td>
      <td className={cell} title={said ? `${now.resolved} of ${said} who said` : "Nobody said"}>
        {percent(now.resolution_rate)}
        <span className={prev}>{percent(before.resolution_rate)}</span>
      </td>
      <td className={cell}>
        {formatUsd(now.spend_usd)}
        <span className={prev}>{formatUsd(before.spend_usd)}</span>
      </td>
      <td className={cell}>
        {usd(now.cost_per_resolved_usd)}
        <span className={prev}>{usd(before.cost_per_resolved_usd)}</span>
      </td>
    </tr>
  );
}
