import { describe, expect, it } from "vitest";

import { audienceWords, categoriesOf } from "./listing";

describe("an assistant's listing in words (Phase 8.5)", () => {
  const groups = { g1: "IT", g2: "HR" };
  const people = { u1: "Pat", u2: "Sam", u3: "Ana", u4: "Lee", u5: "Kim" };

  it("names who it is for", () => {
    expect(audienceWords({ kind: "everyone" }, groups, people)).toBe(
      "Everyone in the organisation",
    );
    expect(audienceWords(null, groups, people)).toBe("Everyone in the organisation");
    expect(audienceWords({ kind: "groups", group_ids: ["g1", "g2"] }, groups, people)).toBe(
      "Members of IT and HR",
    );
    expect(audienceWords({ kind: "people", user_ids: ["u1"] }, groups, people)).toBe("Pat");
    expect(
      audienceWords({ kind: "people", user_ids: ["u1", "u2", "u3", "u4", "u5"] }, groups, people),
    ).toBe("Pat, Sam, Ana and 2 more");
  });

  it("says when a name is gone", () => {
    expect(audienceWords({ kind: "groups", group_ids: ["gone"] }, groups, people)).toBe(
      "Members of a removed group",
    );
  });

  it("lists categories once each, in order", () => {
    expect(categoriesOf([{ category: "IT" }, { category: "it" }, { category: "HR" }, {}])).toEqual([
      "HR",
      "IT",
    ]);
  });
});
