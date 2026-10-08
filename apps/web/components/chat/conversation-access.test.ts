import { describe, expect, it } from "vitest";

import { canAct } from "./conversation-access";

describe("canAct", () => {
  it("lets the person who started a conversation act in it", () => {
    expect(canAct({ created_by: "u1" }, "u1")).toBe(true);
  });

  it("gives everyone else, admins included, a read-only view", () => {
    expect(canAct({ created_by: "u1" }, "u2")).toBe(false);
  });

  it("leaves a conversation whose starter is gone read-only", () => {
    expect(canAct({ created_by: null }, "u1")).toBe(false);
    expect(canAct({}, "u1")).toBe(false);
  });

  it("offers nothing before the signed-in user or the conversation is known", () => {
    expect(canAct({ created_by: "u1" }, null)).toBe(false);
    expect(canAct(undefined, "u1")).toBe(false);
  });
});
