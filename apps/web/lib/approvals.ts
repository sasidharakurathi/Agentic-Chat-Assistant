/** 298 -> "4:58", 9 -> "0:09". */
export function formatCountdown(seconds: number): string {
  const s = Math.max(0, Math.floor(seconds));
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
}

/** Under this many seconds left, the countdown says so loudly. */
export const URGENT_S = 30;

/** The tab's title while approvals wait, so a person in another tab sees
 *  that the assistant is stuck on them. */
export function titleWithPending(title: string, pending: number): string {
  const base = title.replace(/^\(\d+\) Approval needed · /, "");
  return pending > 0 ? `(${pending}) Approval needed · ${base}` : base;
}

/** Whether the input says more than the statement shown: an MCP tool's
 *  arguments, an HTTP body. A SQL call's input is its statement. */
export function inputAddsDetail(rationale: string, input: Record<string, unknown>): boolean {
  const values = Object.values(input).filter((v) => v !== null && v !== "");
  if (values.length === 0) return false;
  return !(
    values.length <= 2 && values.every((v) => typeof v === "string" && rationale.includes(v))
  );
}
