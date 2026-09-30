import { describe, expect, it } from "vitest";

import { carryShared, followsShared, pickModel, selectedModel, setTurns } from "./subagent-models";

const shared = { model: "claude-haiku-4-5", effort: "low" };

describe("subagent role models", () => {
  it("a turn limit alone copies the shared model, effort included", () => {
    const own = setTurns(undefined, shared, 5);
    expect(own).toEqual({ model: "claude-haiku-4-5", effort: "low", max_turns: 5 });
    expect(followsShared(own!, shared)).toBe(true);
    expect(selectedModel(own!, shared)).toBe("");
  });

  it("dropping the limit of a following role removes its settings", () => {
    expect(setTurns({ ...shared, max_turns: 5 }, shared, null)).toBeNull();
    expect(setTurns(undefined, shared, null)).toBeNull();
  });

  it("dropping the limit of a role with its own model keeps the model", () => {
    const own = { model: "claude-sonnet-5", effort: "medium", max_turns: 5 };
    expect(setTurns(own, shared, null)).toEqual({ ...own, max_turns: null });
  });

  it("choosing a model keeps the role's limit, and 'shared' keeps only the limit", () => {
    const own = pickModel({ ...shared, max_turns: 4 }, shared, "claude-sonnet-5");
    expect(own).toEqual({ model: "claude-sonnet-5", effort: "low", max_turns: 4 });
    expect(selectedModel(own!, shared)).toBe("claude-sonnet-5");
    expect(pickModel(own!, shared, "")).toEqual({ ...shared, max_turns: 4 });
    expect(pickModel({ model: "claude-sonnet-5" }, shared, "")).toBeNull();
  });

  it("a shared-model change carries to following roles only", () => {
    const own = {
      retrieval: { ...shared, max_turns: 3 },
      sql: { model: "claude-sonnet-5", effort: "medium" },
    };
    const next = carryShared(own, shared, { model: "claude-sonnet-5" });
    expect(next.retrieval).toEqual({ model: "claude-sonnet-5", effort: "low", max_turns: 3 });
    expect(next.sql).toBe(own.sql);
  });
});
