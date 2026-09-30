import { describe, expect, it } from "vitest";

import { mergeRules, parseRules, sourceNote } from "./prompt-assist";

describe("parseRules", () => {
  it("takes one rule per line and drops blanks", () => {
    expect(parseRules("  Be kind. \n\n Cite sources.\n  ")).toEqual(["Be kind.", "Cite sources."]);
  });
});

describe("mergeRules", () => {
  it("keeps every existing rule, in order, and adds only new ones", () => {
    expect(
      mergeRules(
        ["Never share prices.", "Be brief."],
        ["be  brief", "Ask before refunds.", "Ask before refunds."],
      ),
    ).toEqual(["Never share prices.", "Be brief.", "Ask before refunds."]);
  });

  it("adds nothing blank", () => {
    expect(mergeRules([], ["  ", "Be kind."])).toEqual(["Be kind."]);
  });
});

describe("sourceNote", () => {
  it("says a template draft was free", () => {
    expect(sourceNote({ source: "template", model: null, cost_usd: 0 })).toMatch(/template.*free/);
  });

  it("names the model and the cost of a model draft", () => {
    expect(sourceNote({ source: "model", model: "claude-sonnet-5", cost_usd: 0.0058 })).toBe(
      "Written by claude-sonnet-5 · under $0.01, on this assistant's usage.",
    );
    expect(sourceNote({ source: "model", model: "claude-opus-5", cost_usd: 0.123 })).toContain(
      "$0.12",
    );
  });

  it("takes the verb for what was made", () => {
    expect(sourceNote({ source: "template", model: null, cost_usd: 0 }, "Recommended")).toMatch(
      /^Recommended from a template/,
    );
  });
});
