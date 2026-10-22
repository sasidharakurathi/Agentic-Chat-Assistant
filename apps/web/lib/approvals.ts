/** Requests that wait for a person (Phase 9.1): stored, decided by the asker
 *  or an approver, run once by the server, and posted back. These helpers
 *  say where one stands and when to look again. */

/** Still to happen: waiting for someone, or about to run, or running. */
export const OPEN_STATES: ReadonlySet<string> = new Set(["pending", "approved", "executing"]);

export function isOpen(status: string): boolean {
  return OPEN_STATES.has(status);
}

/** How soon to look again: soon while one is about to run or running,
 *  now and then while one waits (it may be decided in another tab, or by
 *  an approver hours later), not at all once they have all ended. */
export function pollDelayMs(statuses: string[]): number | null {
  if (statuses.some((s) => s === "approved" || s === "executing")) return 2500;
  if (statuses.includes("pending")) return 20_000;
  return null;
}

/** Where a request stands, in words. `mine`: the person reading asked. */
export function requestLine(status: string, tier: string, mine: boolean): string {
  switch (status) {
    case "pending":
      if (tier === "asker")
        return mine ? "Waiting for you to confirm" : "Waiting for them to confirm";
      return "Waiting for an approver";
    case "approved":
      return "Approved, about to run";
    case "executing":
      return "Running now";
    case "succeeded":
      return "Done";
    case "failed":
      return "Didn't go through";
    case "denied":
      return "Declined";
    case "expired":
      return "Not decided in time";
    default:
      return "Withdrawn";
  }
}

/** "closes in 40 minutes", "closes in 5 hours", "closes in 3 days". */
export function closesIn(expiresAt: string | null | undefined, now = Date.now()): string | null {
  if (!expiresAt) return null;
  const ms = new Date(expiresAt).getTime() - now;
  if (Number.isNaN(ms)) return null;
  if (ms <= 0) return "closed";
  const minutes = Math.round(ms / 60_000);
  if (minutes < 60) return `closes in ${Math.max(1, minutes)} minute${minutes === 1 ? "" : "s"}`;
  const hours = Math.round(ms / 3_600_000);
  if (hours < 48) return `closes in ${hours} hour${hours === 1 ? "" : "s"}`;
  const days = Math.round(ms / 86_400_000);
  return `closes in ${days} days`;
}

/** The values an asker sends back from their card: what they typed, as
 *  the server reads it (it checks each against the action's own types). */
export function editedParams(
  fields: { name: string; type: string }[],
  values: Record<string, string | boolean>,
): Record<string, unknown> {
  const out: Record<string, unknown> = {};
  for (const f of fields) {
    const v = values[f.name];
    if (f.type === "boolean") out[f.name] = v === true || v === "true";
    else if (typeof v === "string" && v.trim() !== "") out[f.name] = v;
  }
  return out;
}

/** A value as a form field shows it. */
export function asText(value: unknown): string {
  if (value === null || value === undefined) return "";
  return typeof value === "string" ? value : String(value);
}

/** The tab's title while a request waits on the person reading it, so
 *  they see it from another tab. */
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

/** The card's question: what is being asked, in plain words. A database
 *  write says so; other tools are named the way people read them. */
export function approvalQuestion(tool: string): string {
  const m = /^mcp__([^_]+(?:-[^_]+)*)__(.+)$/.exec(tool);
  if (!m) return tool === "WebSearch" ? "Approve this web search?" : `Approve ${tool}?`;
  const [, server, name] = m;
  if (server !== "caps") return `Approve ${name} on ${server}?`;
  if (/^(sql|mongo)_/.test(name)) return "Approve this database change?";
  if (name === "http_request") return "Approve this web request?";
  if (name === "memory") return "Approve this change to its memory?";
  return `Approve ${name}?`;
}

/** "Low risk", "Medium risk", "High risk". */
export function riskLabel(risk: string): string {
  const word = risk ? risk[0].toUpperCase() + risk.slice(1) : "Unknown";
  return `${word} risk`;
}
