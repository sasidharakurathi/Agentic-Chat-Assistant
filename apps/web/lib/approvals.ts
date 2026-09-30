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

/** The approval card's question: what is being asked, in plain words. A
 *  database write says so; other tools are named the way people read them. */
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
