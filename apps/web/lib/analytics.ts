/** Analytics in words (Phase 10.1) and traffic on the canvas (Phase 10.2).
 *  Pure helpers, so the words and the drawing rules are tested on their
 *  own. */

import type { AnalyticsFigures, Graph, NodeTraffic } from "@/lib/api";
import { formatUsd } from "@/lib/format";

/** The periods the analytics compare, in days. */
export const PERIODS = [
  { value: "7", label: "7 days" },
  { value: "30", label: "30 days" },
  { value: "90", label: "90 days" },
] as const;
export type Period = (typeof PERIODS)[number]["value"];

function plural(n: number, one: string, many: string): string {
  return `${n} ${n === 1 ? one : many}`;
}

/** 0.6 -> "60%"; none -> "–". */
export function percent(rate: number | null): string {
  return rate === null ? "–" : `${Math.round(rate * 100)}%`;
}

/** A figure next to the previous period's, in words: "Up from 12 in the
 *  previous 30 days". No percentage of a percentage, and no colour: up is
 *  good for resolved conversations and bad for cost. */
export function compareWords(
  now: number | null,
  before: number | null,
  days: number,
  show: (n: number) => string = String,
): string {
  const period = `the previous ${plural(days, "day", "days")}`;
  if (before === null) return `No figure for ${period}`;
  if (now === null) return `${show(before)} in ${period}`;
  if (now === before) return `Same as ${period}`;
  return `${now > before ? "Up" : "Down"} from ${show(before)} in ${period}`;
}

/** "3 resolved, 2 not resolved and 1 with no verdict." */
export function verdictWords(f: AnalyticsFigures): string {
  if (f.conversations === 0) return "No conversations in this period.";
  return `${f.resolved} resolved, ${f.not_resolved} not resolved and ${f.no_verdict} with no verdict.`;
}

/** The resolution rate with how many said: "60% of the 5 who said". */
export function rateWords(f: AnalyticsFigures): string {
  const said = f.resolved + f.not_resolved;
  if (said === 0) return "Nobody said yet";
  return `${percent(f.resolution_rate)} of the ${plural(said, "person", "people")} who said`;
}

export const usd = (n: number | null): string => (n === null ? "–" : formatUsd(n));

// ── traffic on the canvas ────────────────────────────────────

const MAIN_LINE = new Set(["input", "guardrail", "router", "agent", "output"]);

const busy = (c: NodeTraffic | undefined) => c !== undefined && c.calls + c.approvals > 0;

/** The lines that carried traffic, as "source>target" keys. The main line
 *  is busy when the agent answered anything. A capability line is busy
 *  when its capability was called (a data source's line when its
 *  knowledge base was): never just because the agent at its other end
 *  answered something. */
export function busyEdges(graph: Graph, nodes: Record<string, NodeTraffic>): Set<string> {
  const typeOf = new Map(graph.nodes.map((n) => [n.id, n.type]));
  const agentBusy = graph.nodes.some((n) => n.type === "agent" && busy(nodes[n.id]));
  const out = new Set<string>();
  for (const e of graph.edges) {
    const from = typeOf.get(e.source);
    const to = typeOf.get(e.target);
    if (!from || !to) continue;
    const lit =
      MAIN_LINE.has(from) && MAIN_LINE.has(to)
        ? agentBusy
        : busy(nodes[e.source]) || (to !== "agent" && to !== "subagent" && busy(nodes[e.target]));
    if (lit) out.add(`${e.source}>${e.target}`);
  }
  return out;
}

/** What a station's counts say, word by word: the agent answers
 *  messages; everything else is called. */
export function trafficChips(
  type: string,
  c: NodeTraffic | undefined,
): { tone: "plain" | "destructive" | "warning"; text: string }[] {
  if (!c) return [];
  const chips: { tone: "plain" | "destructive" | "warning"; text: string }[] = [];
  if (c.calls > 0) {
    chips.push({
      tone: "plain",
      text:
        type === "agent"
          ? plural(c.calls, "message", "messages")
          : plural(c.calls, "call", "calls"),
    });
  }
  if (c.errors > 0) chips.push({ tone: "destructive", text: plural(c.errors, "error", "errors") });
  if (c.approvals > 0) {
    chips.push({ tone: "warning", text: plural(c.approvals, "approval", "approvals") });
  }
  return chips;
}
