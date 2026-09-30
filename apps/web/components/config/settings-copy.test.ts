import { describe, expect, it } from "vitest";

import { describeSource } from "@/lib/prompt-assist";
import { modelName } from "@/lib/subagent-models";

describe("modelName", () => {
  it("reads model ids in plain words", () => {
    expect(modelName("claude-haiku-4-5")).toBe("Haiku 4.5");
    expect(modelName("claude-sonnet-5")).toBe("Sonnet 5");
    expect(modelName("claude-opus-4-1-20250805")).toBe("Opus 4.1");
  });

  it("leaves other ids as they are", () => {
    expect(modelName("gpt-x")).toBe("gpt-x");
    expect(modelName("")).toBe("");
  });
});

describe("describeSource", () => {
  it("says who wrote it and what it cost, without a middle dot", () => {
    const text = describeSource({ source: "model", model: "claude-sonnet-5", cost_usd: 0.0058 });
    expect(text).toBe("Written by Sonnet 5 for under $0.01, charged to this assistant's usage.");
    expect(text).not.toContain("·");
  });

  it("says a template draft was free", () => {
    expect(describeSource({ source: "template", model: null, cost_usd: 0 }, "Suggested")).toMatch(
      /^Suggested from a template\..*free\.$/,
    );
  });
});
