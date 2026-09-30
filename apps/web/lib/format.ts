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
