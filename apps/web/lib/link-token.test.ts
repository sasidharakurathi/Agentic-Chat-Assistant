import { describe, expect, it } from "vitest";

import { tokenFromHash } from "./link-token";

describe("the token in an emailed link (Phase 7b.6)", () => {
  it("is read from after the #", () => {
    expect(tokenFromHash("#token=abcdefghijklmnop")).toBe("abcdefghijklmnop");
    expect(tokenFromHash("token=abcdefghijklmnop")).toBe("abcdefghijklmnop");
  });

  it("is nothing when missing or too short to be one", () => {
    expect(tokenFromHash("")).toBeNull();
    expect(tokenFromHash("#token=short")).toBeNull();
    expect(tokenFromHash("#other=abcdefghijklmnop")).toBeNull();
  });
});
