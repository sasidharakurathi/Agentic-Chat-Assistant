/** What an MCP tool's approval settings add up to (tasks 4.7 / 4.10).
 *
 *  Mirrors the server exactly, so the canvas can say what will happen
 *  instead of leaving a builder to work it out:
 *
 *  - `approvals.mcp_mode`: the most specific rule set wins (the tool's, else
 *    the server's, else the assistant's `mcp_default`), except that
 *    `mcp_default: deny` switches every MCP tool off.
 *  - `approvals.classify` + the router: `auto` skips the person only for a
 *    tool the server declares read-only; anything else still asks. */

export type Mode = "auto" | "require" | "deny";
export type Outcome = "runs" | "asks" | "never";

export function effectiveMode(
  assistantDefault: Mode,
  serverRule: Mode | null | undefined,
  toolRule: Mode | null | undefined,
): Mode {
  if (assistantDefault === "deny") return "deny";
  return toolRule ?? serverRule ?? assistantDefault;
}

export function outcome(mode: Mode, readOnly: boolean | null | undefined): Outcome {
  if (mode === "deny") return "never";
  if (mode === "auto" && readOnly === true) return "runs";
  return "asks";
}

export const OUTCOME_TEXT: Record<Outcome, string> = {
  runs: "Runs without asking",
  asks: "Asks a person first",
  never: "Never runs",
};

export const MODE_TEXT: Record<Mode, string> = {
  require: "Ask a person first",
  auto: "Run without asking (read-only tools)",
  deny: "Never allow",
};

export const asMode = (v: unknown): Mode | null =>
  v === "auto" || v === "require" || v === "deny" ? v : null;
