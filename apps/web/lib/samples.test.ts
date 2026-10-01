import { describe, expect, it } from "vitest";

import { sampleExtras, sampleStops } from "./samples";

describe("sampleStops", () => {
  it("leaves out the stations every assistant has", () => {
    expect(sampleStops(["input", "guardrail", "agent", "output"])).toEqual([]);
  });

  it("counts each kind once, knowledge before actions before helpers", () => {
    const stops = sampleStops([
      "input",
      "guardrail",
      "memory",
      "agent",
      "output",
      "router",
      "tool",
      "tool",
      "tool",
      "subagent",
      "knowledge_base",
    ]);
    expect(stops).toEqual([
      { type: "knowledge_base", count: 1 },
      { type: "memory", count: 1 },
      { type: "tool", count: 3 },
      { type: "subagent", count: 1 },
      { type: "router", count: 1 },
    ]);
  });

  it("keeps a kind it does not know, after the ones it does", () => {
    expect(sampleStops(["tool", "hologram"]).map((s) => s.type)).toEqual(["tool", "hologram"]);
  });
});

describe("sampleExtras", () => {
  it("says what a sample brings, in words", () => {
    expect(sampleExtras(3, 5)).toBe("Comes with 3 documents and 5 test questions.");
    expect(sampleExtras(1, 1)).toBe("Comes with 1 document and 1 test question.");
    expect(sampleExtras(0, 2)).toBe("Comes with 2 test questions.");
  });

  it("says nothing when it brings nothing", () => {
    expect(sampleExtras(0, 0)).toBeNull();
  });
});
