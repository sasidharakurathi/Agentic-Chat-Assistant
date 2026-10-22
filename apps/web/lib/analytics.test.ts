import { describe, expect, it } from "vitest";

import type { AnalyticsFigures, Graph } from "./api";
import {
  busyEdges,
  compareWords,
  percent,
  rateWords,
  trafficChips,
  verdictWords,
} from "./analytics";

const figures = (over: Partial<AnalyticsFigures> = {}): AnalyticsFigures => ({
  conversations: 6,
  people: 2,
  resolved: 3,
  not_resolved: 2,
  no_verdict: 1,
  with_errors: 1,
  spend_usd: 0.5,
  resolution_rate: 0.6,
  cost_per_resolved_usd: 0.1667,
  ...over,
});

describe("analytics in words (Phase 10.1)", () => {
  it("compares with the previous period without colouring it", () => {
    expect(compareWords(12, 9, 30)).toBe("Up from 9 in the previous 30 days");
    expect(compareWords(3, 9, 7)).toBe("Down from 9 in the previous 7 days");
    expect(compareWords(4, 4, 1)).toBe("Same as the previous 1 day");
    expect(compareWords(0.6, null, 30)).toBe("No figure for the previous 30 days");
    expect(compareWords(null, 0.5, 30, percent)).toBe("50% in the previous 30 days");
  });

  it("says the rate with how many said, and the verdicts", () => {
    expect(rateWords(figures())).toBe("60% of the 5 people who said");
    expect(rateWords(figures({ resolved: 1, not_resolved: 0, resolution_rate: 1 }))).toBe(
      "100% of the 1 person who said",
    );
    expect(rateWords(figures({ resolved: 0, not_resolved: 0, resolution_rate: null }))).toBe(
      "Nobody said yet",
    );
    expect(verdictWords(figures())).toBe("3 resolved, 2 not resolved and 1 with no verdict.");
    expect(verdictWords(figures({ conversations: 0 }))).toBe("No conversations in this period.");
  });
});

describe("traffic on the canvas (Phase 10.2)", () => {
  const node = (id: string, type: string) => ({ id, type, position: { x: 0, y: 0 }, data: {} });
  const graph = {
    schema_version: 1,
    nodes: [
      node("in", "input"),
      node("agent", "agent"),
      node("out", "output"),
      node("kb", "knowledge_base"),
      node("ds", "data_source"),
      node("db", "database"),
    ],
    edges: [
      { source: "in", target: "agent" },
      { source: "agent", target: "out" },
      { source: "kb", target: "agent" },
      { source: "ds", target: "kb" },
      { source: "db", target: "agent" },
    ],
  } as unknown as Graph;

  it("lights the main line and the lines of what was called, not the rest", () => {
    const lit = busyEdges(graph, {
      agent: { calls: 4, errors: 0, approvals: 0 },
      kb: { calls: 2, errors: 0, approvals: 0 },
    });
    expect([...lit].sort()).toEqual(["agent>out", "ds>kb", "in>agent", "kb>agent"]);
    expect(busyEdges(graph, {}).size).toBe(0);
  });

  it("words a station's counts", () => {
    expect(trafficChips("agent", { calls: 1, errors: 1, approvals: 0 })).toEqual([
      { tone: "plain", text: "1 message" },
      { tone: "destructive", text: "1 error" },
    ]);
    expect(trafficChips("database", { calls: 3, errors: 0, approvals: 2 })).toEqual([
      { tone: "plain", text: "3 calls" },
      { tone: "warning", text: "2 approvals" },
    ]);
    expect(trafficChips("tool", undefined)).toEqual([]);
  });
});
