"use client";

import { useEffect, useId, useState } from "react";

import { Alert } from "@/components/ui/alert";
import { Loading } from "@/components/ui/loading";
import { SectionHeading } from "@/components/ui/section-heading";
import { Segmented } from "@/components/ui/segmented";
import {
  PERIODS,
  compareWords,
  percent,
  rateWords,
  usd,
  verdictWords,
  type Period,
} from "@/lib/analytics";
import { ApiError, analytics, type AnalyticsDay, type AssistantAnalytics } from "@/lib/api";
import { failureMessage, formatDate, formatUsd } from "@/lib/format";

/** How an assistant's conversations in Chat went (Phase 10.1), for its
 *  builders and admins: conversations, how many were resolved (only when
 *  the person said so), spend and cost per resolved conversation, each
 *  against the period before, and a daily chart. Counts only: never who
 *  asked or what. */
export function AnalyticsPanel({ assistantId }: { assistantId: string }) {
  const [period, setPeriod] = useState<Period>("30");
  const [data, setData] = useState<AssistantAnalytics | null>(null);
  const [error, setError] = useState<string | null>(null);
  const id = useId();

  useEffect(() => {
    let live = true;
    setError(null);
    analytics
      .assistant(assistantId, Number(period))
      .then((d) => live && setData(d))
      .catch((err) => {
        if (!live) return;
        setError(
          err instanceof ApiError && err.status === 403
            ? "Only this assistant's editors and admins can see its analytics."
            : failureMessage(
                "Couldn't load the analytics.",
                err instanceof ApiError ? err.message : null,
                "Check your connection, then reload the page.",
              ),
        );
      });
    return () => {
      live = false;
    };
  }, [assistantId, period]);

  const days = Number(period);
  const now = data?.current;
  const before = data?.previous;

  return (
    <section aria-labelledby={`${id}-heading`} className="flex max-w-200 flex-col gap-8">
      <SectionHeading
        level={2}
        id={`${id}-heading`}
        title="Analytics"
        description="How its conversations in Chat went. A conversation counts as resolved only when the person said it solved their question. Previews and evals aren't counted."
        actions={
          <Segmented
            label="Period"
            value={period}
            onChange={setPeriod}
            options={PERIODS.map((p) => ({ value: p.value, label: p.label }))}
          />
        }
      />

      {error && <Alert>{error}</Alert>}
      {!data && !error && <Loading what="analytics" rows={3} />}

      {data && now && before && (
        <>
          <dl className="border-border grid grid-cols-1 border-t sm:grid-cols-2 lg:grid-cols-4">
            <Figure
              label="Conversations"
              value={String(now.conversations)}
              change={compareWords(now.conversations, before.conversations, days)}
            />
            <Figure
              label="Resolved"
              value={percent(now.resolution_rate)}
              detail={rateWords(now)}
              change={compareWords(now.resolution_rate, before.resolution_rate, days, (r) =>
                percent(r),
              )}
            />
            <Figure
              label="Spend"
              value={formatUsd(now.spend_usd)}
              change={compareWords(now.spend_usd, before.spend_usd, days, formatUsd)}
            />
            <Figure
              label="Cost per resolved conversation"
              value={usd(now.cost_per_resolved_usd)}
              change={compareWords(
                now.cost_per_resolved_usd,
                before.cost_per_resolved_usd,
                days,
                formatUsd,
              )}
            />
          </dl>
          <p>
            {verdictWords(now)}{" "}
            {now.conversations > 0 && (
              <span className="text-muted-foreground">
                {now.people} {now.people === 1 ? "person" : "people"} started them;{" "}
                {now.with_errors} had an error.
              </span>
            )}
          </p>
          <DailyChart series={data.series} />
          <details className="text-small text-muted-foreground max-w-[60ch]">
            <summary className="cursor-pointer">How these are counted</summary>
            <ul className="mt-2 flex list-disc flex-col gap-1 pl-5">
              <li>Resolved: the person answered yes to &ldquo;Did this solve it?&rdquo;</li>
              <li>
                Not resolved: they answered no, or gave an answer a thumbs down and never said yes.
              </li>
              <li>No verdict: they didn&rsquo;t say. That counts as neither.</li>
              <li>
                The resolved rate is of the people who said. Spend is what these conversations cost
                in the period, divided by the resolved ones for the cost per resolved conversation.
              </li>
              <li>
                Days are UTC days, ending today. The change is against the period just before.
              </li>
            </ul>
          </details>
        </>
      )}
    </section>
  );
}

function Figure({
  label,
  value,
  detail,
  change,
}: {
  label: string;
  value: string;
  detail?: string;
  change: string;
}) {
  return (
    <div className="border-border flex flex-col gap-0.5 border-b py-3 pr-4">
      <dt className="text-label text-muted-foreground">{label}</dt>
      <dd className="num text-h2 font-semibold">{value}</dd>
      {detail && <dd className="text-small">{detail}</dd>}
      <dd className="text-small text-muted-foreground">{change}</dd>
    </div>
  );
}

/** Conversations per day as muted bars, with the resolved part of each in
 *  ink. Plain SVG; the numbers are also in a table for screen readers, and
 *  each day says its own on hover. */
function DailyChart({ series }: { series: AnalyticsDay[] }) {
  const headingId = useId();
  const max = Math.max(1, ...series.map((d) => d.conversations));
  const slot = 12;
  const bar = 8;
  const height = 120;
  const width = series.length * slot;
  const y = (n: number) => (n / max) * height;
  const first = series[0]?.date;
  const last = series[series.length - 1]?.date;
  return (
    <figure aria-labelledby={headingId} className="flex flex-col gap-2">
      <figcaption className="flex flex-wrap items-center justify-between gap-2">
        <h3 id={headingId} className="text-h3 font-semibold">
          Conversations each day
        </h3>
        <span className="text-small text-muted-foreground flex items-center gap-4">
          <span className="flex items-center gap-1.5">
            <span aria-hidden className="bg-muted-foreground/35 inline-block size-2.5 rounded-sm" />
            Conversations
          </span>
          <span className="flex items-center gap-1.5">
            <span aria-hidden className="bg-primary inline-block size-2.5 rounded-sm" />
            Resolved
          </span>
        </span>
      </figcaption>
      <div className="flex gap-2">
        <div
          aria-hidden
          className="text-small text-muted-foreground num flex h-[120px] flex-col justify-between text-right"
        >
          <span>{max}</span>
          <span>0</span>
        </div>
        <svg
          aria-hidden
          viewBox={`0 0 ${width} ${height}`}
          preserveAspectRatio="none"
          className="border-border h-[120px] min-w-0 flex-1 border-b"
        >
          {series.map((d, i) => {
            const x = i * slot + (slot - bar) / 2;
            return (
              <g key={d.date}>
                <title>{`${formatDate(d.date)}: ${d.conversations} ${
                  d.conversations === 1 ? "conversation" : "conversations"
                }, ${d.resolved} resolved`}</title>
                <rect
                  x={i * slot}
                  y={0}
                  width={slot}
                  height={height}
                  className="fill-transparent"
                />
                {d.conversations > 0 && (
                  <rect
                    x={x}
                    y={height - y(d.conversations)}
                    width={bar}
                    height={y(d.conversations)}
                    className="fill-muted-foreground/35"
                  />
                )}
                {d.resolved > 0 && (
                  <rect
                    x={x}
                    y={height - y(d.resolved)}
                    width={bar}
                    height={y(d.resolved)}
                    className="fill-primary"
                  />
                )}
              </g>
            );
          })}
        </svg>
      </div>
      {first && last && (
        <div className="text-small text-muted-foreground flex justify-between pl-6">
          <span>{formatDate(first)}</span>
          <span>{formatDate(last)}</span>
        </div>
      )}
      <table className="sr-only">
        <caption>Conversations and resolved conversations each day</caption>
        <thead>
          <tr>
            <th scope="col">Day</th>
            <th scope="col">Conversations</th>
            <th scope="col">Resolved</th>
          </tr>
        </thead>
        <tbody>
          {series.map((d) => (
            <tr key={d.date}>
              <td>{formatDate(d.date)}</td>
              <td>{d.conversations}</td>
              <td>{d.resolved}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </figure>
  );
}
