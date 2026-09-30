import type { BudgetStatus } from "@/lib/api";

/** A limit as typed: "" means none, a number of at least a cent is a limit,
 *  anything else is `undefined` (not valid). */
export function parseLimit(text: string): number | null | undefined {
  const t = text.trim().replace(/^\$/, "");
  if (t === "") return null;
  const n = Number(t);
  return Number.isFinite(n) && n >= 0.01 ? Math.round(n * 100) / 100 : undefined;
}

/** The meter's fill: green, ochre from 80%, red at 100%. */
export function budgetTone(state: BudgetStatus["state"]): string {
  return state === "exceeded"
    ? "bg-destructive"
    : state === "warning"
      ? "bg-warning"
      : "bg-success";
}

/** How full the bar is, 0 to 100: rounded down, as the chat's warning
 *  says it, so 99.6% never reads as a full "100%" that isn't stopped. */
export function budgetPercent(b: Pick<BudgetStatus, "ratio">): number {
  return Math.max(0, Math.min(100, Math.floor(b.ratio * 100)));
}

/** Whose limit and for how long: "Daily limit for the organization",
 *  "Monthly limit for Helper". */
export function budgetTitle(b: Pick<BudgetStatus, "scope" | "period" | "assistant_name">): string {
  const when = b.period === "day" ? "Daily" : "Monthly";
  return b.scope === "org"
    ? `${when} limit for the organization`
    : `${when} limit for ${b.assistant_name ?? "this assistant"}`;
}

/** "Resets at midnight UTC" or "Resets 1 Nov (UTC)". */
export function resetsLabel(b: Pick<BudgetStatus, "period" | "resets_at">): string {
  if (b.period === "day") return "Resets at midnight UTC";
  const d = new Date(b.resets_at);
  const month = d.toLocaleString("en-GB", { month: "short", timeZone: "UTC" });
  return `Resets ${d.getUTCDate()} ${month} (UTC)`;
}

export type Range = "today" | "month" | "30d";

/** Where a dashboard range starts, as the API's `from` (UTC). */
export function rangeStart(range: Range, now = new Date()): string {
  const y = now.getUTCFullYear();
  const m = now.getUTCMonth();
  if (range === "today") return new Date(Date.UTC(y, m, now.getUTCDate())).toISOString();
  if (range === "month") return new Date(Date.UTC(y, m, 1)).toISOString();
  return new Date(now.getTime() - 30 * 24 * 60 * 60 * 1000).toISOString();
}

/** The limits a scope has now, to fill its form. */
export function limitsOf(
  list: Pick<BudgetStatus, "scope" | "assistant_id" | "period" | "limit_usd">[],
  assistantId: string | null,
): { daily: string; monthly: string } {
  const find = (period: "day" | "month") =>
    list.find(
      (b) =>
        b.period === period &&
        (assistantId === null ? b.scope === "org" : b.assistant_id === assistantId),
    );
  const text = (b: { limit_usd: number } | undefined) => (b ? String(b.limit_usd) : "");
  return { daily: text(find("day")), monthly: text(find("month")) };
}
