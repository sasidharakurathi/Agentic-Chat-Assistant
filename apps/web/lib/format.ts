/** A dollar amount at the precision it needs: a chat turn often costs a
 *  fraction of a cent, which `toFixed(2)` would show as "$0.00", and a
 *  conversation can run to dollars, where four decimals are noise. */
export function formatUsd(amount: number | string): string {
  const n = typeof amount === "string" ? Number(amount) : amount;
  if (!Number.isFinite(n) || n <= 0) return "$0.00";
  if (n < 0.0001) return "<$0.0001";
  // Four places below a dollar, without zeros past the cents: "$0.50",
  // "$0.045", "$0.0042" (a limit of $0.50 read "$0.5000").
  if (n < 1) {
    const short = String(Number(n.toFixed(4)));
    return `$${(short.split(".")[1] ?? "").length < 2 ? n.toFixed(2) : short}`;
  }
  return `$${n.toFixed(2)}`;
}

/** 950 -> "950", 12_345 -> "12.3k", 2_500_000 -> "2.5M". */
export function formatCount(n: number): string {
  if (n < 1000) return String(n);
  if (n < 1_000_000) return `${(n / 1000).toFixed(n < 10_000 ? 1 : 0)}k`;
  return `${(n / 1_000_000).toFixed(1)}M`;
}

const MINUTE = 60_000;
const HOUR = 60 * MINUTE;
const DAY = 24 * HOUR;

function plural(n: number, one: string, many: string): string {
  return `${n} ${n === 1 ? one : many}`;
}

/** How long ago something happened, in plain words: "just now",
 *  "5 minutes ago", "yesterday", "4 days ago", then a date ("12 Sep", or
 *  "12 Sep 2025" in another year). A time in the future reads "just now". */
export function formatRelative(iso: string, now: Date = new Date()): string {
  const then = new Date(iso);
  if (Number.isNaN(then.getTime())) return "";
  const ago = now.getTime() - then.getTime();
  if (ago < MINUTE) return "just now";
  if (ago < HOUR) return `${plural(Math.floor(ago / MINUTE), "minute", "minutes")} ago`;
  if (ago < DAY) return `${plural(Math.floor(ago / HOUR), "hour", "hours")} ago`;
  const days = Math.floor(ago / DAY);
  if (days === 1) return "yesterday";
  if (days < 7) return `${days} days ago`;
  return formatDate(iso, now);
}

/** "12 Sep", or "12 Sep 2025" when it is not this year. */
export function formatDate(iso: string, now: Date = new Date()): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "";
  const sameYear = d.getFullYear() === now.getFullYear();
  return d.toLocaleDateString("en-GB", {
    day: "numeric",
    month: "short",
    ...(sameYear ? {} : { year: "numeric" }),
  });
}

/** An error in the order the design asks for: what failed, why (the
 *  server's own message, or that it could not be reached), then what to
 *  do. `reason` is the ApiError message, or null for a network failure. */
export function failureMessage(what: string, reason: string | null, todo: string): string {
  const why = reason?.trim().replace(/[.!?]+$/, "");
  return `${what} ${why ? `${why}.` : "The server couldn't be reached."} ${todo}`;
}
