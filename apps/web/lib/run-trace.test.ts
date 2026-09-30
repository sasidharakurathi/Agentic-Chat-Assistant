import { describe, expect, it } from "vitest";

import {
  approvalLine,
  canvasHref,
  formatMs,
  mainStops,
  nestSteps,
  parseRunParam,
  runStatus,
  stepTitle,
  stopReasonText,
} from "./run-trace";

describe("stepTitle", () => {
  it("names tools, subagents and guardrails the way people read them", () => {
    expect(stepTitle({ kind: "tool", name: "mcp__caps__sql_query", input: {} })).toBe("sql_query");
    expect(stepTitle({ kind: "tool", name: "mcp__tickets__create", input: {} })).toBe(
      "tickets: create",
    );
    expect(stepTitle({ kind: "tool", name: "Agent", input: { subagent_type: "sql" } })).toBe(
      "sql subagent",
    );
    expect(
      stepTitle({
        kind: "guardrail",
        name: "x",
        input: null,
        guardrail: { check: "injection", where: "user_message", detail: "" },
      }),
    ).toBe("Guardrail: injection");
  });
});

describe("formatMs", () => {
  it("picks a unit", () => {
    expect(formatMs(320)).toBe("320 ms");
    expect(formatMs(1234)).toBe("1.2 s");
    expect(formatMs(65_000)).toBe("1 m 5 s");
  });
});

describe("nestSteps", () => {
  it("puts a subagent's calls under its delegation, in order", () => {
    const steps = [
      { id: "a", parent_id: null },
      { id: "t", parent_id: null },
      { id: "t1", parent_id: "t" },
      { id: "t2", parent_id: "t" },
      { id: "b", parent_id: null },
      { id: "orphan", parent_id: "gone" },
    ];
    expect(nestSteps(steps).map(({ step, depth }) => `${step.id}:${depth}`)).toEqual([
      "a:0",
      "t:0",
      "t1:1",
      "t2:1",
      "b:0",
      "orphan:0",
    ]);
  });
});

describe("approvalLine", () => {
  const base = {
    risk: "high",
    requested_at: "2026-09-29T10:00:00Z",
    decided_at: null,
    decided_by: null,
    wait_ms: null,
  };
  it("says who answered and after how long", () => {
    expect(approvalLine({ ...base, status: "approved", decided_by: "Sam", wait_ms: 12_000 })).toBe(
      "Approved by Sam after 12.0 s",
    );
    expect(approvalLine({ ...base, status: "denied", decided_by: "Sam", wait_ms: 900 })).toBe(
      "Denied by Sam after 900 ms",
    );
    expect(approvalLine({ ...base, status: "expired", wait_ms: 300_000 })).toBe(
      "No answer: declined after 5 m 0 s",
    );
    expect(approvalLine({ ...base, status: "pending" })).toBe("Waiting for someone to answer");
  });
});

describe("the canvas link", () => {
  const cid = "11111111-1111-1111-1111-111111111111";
  const rid = "22222222-2222-2222-2222-222222222222";
  it("round-trips, and rejects anything else", () => {
    const href = canvasHref("a1", cid, rid);
    expect(href).toBe(`/assistants/a1/build?run=${cid}.${rid}`);
    expect(parseRunParam(new URL(href, "http://x").searchParams.get("run"))).toEqual({
      conversationId: cid,
      runId: rid,
    });
    expect(parseRunParam("nonsense")).toBeNull();
    expect(parseRunParam(null)).toBeNull();
  });
});

describe("runStatus", () => {
  it("says how a run ended in words", () => {
    expect(runStatus("ok")).toEqual({ label: "Finished", tone: "success" });
    expect(runStatus("error").tone).toBe("destructive");
    expect(runStatus("aborted").label).toBe("Stopped");
  });
});

describe("stopReasonText", () => {
  it("is silent for a normal end", () => {
    expect(stopReasonText("end_turn")).toBeNull();
    expect(stopReasonText(null)).toBeNull();
    expect(stopReasonText("max_tokens")).toBe("It reached the answer length limit.");
    expect(stopReasonText("odd_reason")).toBe("It stopped early (odd reason).");
  });
});

describe("mainStops", () => {
  const graphTypes = new Map([
    ["i", "input"],
    ["g", "guardrail"],
    ["r", "router"],
    ["a", "agent"],
    ["o", "output"],
    ["kb", "knowledge_base"],
  ]);

  it("numbers the stops the graph has, in route order", () => {
    const stops = mainStops({
      graphTypes,
      touched: ["i", "g", "a", "o", "kb"],
      routed: false,
      guardrailSteps: false,
    });
    expect(stops.map((s) => [s.type, s.number, s.lit])).toEqual([
      ["input", 1, true],
      ["guardrail", 2, true],
      ["router", 3, false],
      ["agent", 4, true],
      ["output", 5, true],
    ]);
  });

  it("closes up the numbers when a stop is missing", () => {
    const noRouter = new Map([...graphTypes].filter(([, t]) => t !== "router"));
    const stops = mainStops({
      graphTypes: noRouter,
      touched: ["i", "a", "o"],
      routed: false,
      guardrailSteps: false,
    });
    expect(stops.map((s) => s.number)).toEqual([1, 2, 3, 4]);
  });

  it("takes the canvas's numbers from the wiring when it has them", () => {
    // The router is on the graph but not wired onto the line: the canvas
    // gives it no number, and neither does the strip.
    const stops = mainStops({
      graphTypes,
      stopNumbers: new Map([
        ["input", 1],
        ["guardrail", 2],
        ["agent", 3],
        ["output", 4],
      ]),
      touched: ["i", "a", "o"],
      routed: false,
      guardrailSteps: false,
    });
    expect(stops.map((s) => [s.type, s.number])).toEqual([
      ["input", 1],
      ["guardrail", 2],
      ["router", undefined],
      ["agent", 3],
      ["output", 4],
    ]);
  });

  it("shows only what the run proves without a graph", () => {
    const stops = mainStops({ graphTypes: null, touched: [], routed: true, guardrailSteps: false });
    expect(stops.map((s) => s.type)).toEqual(["input", "router", "agent", "output"]);
    expect(stops.every((s) => s.number === undefined && s.lit)).toBe(true);
  });
});
