import { describe, expect, it } from "vitest";

import { limitWords, usedShare } from "./people-limits";

describe("per-person limits in words (Phase 8.9)", () => {
  it("says whose limit it is", () => {
    expect(limitWords(null, false)).toBe("No limit");
    expect(limitWords(2, false)).toBe("$2.00");
    expect(limitWords(5, true)).toBe("$5.00 (their own)");
  });

  it("works out how much is used", () => {
    expect(usedShare(0.5, 2)).toBe(25);
    expect(usedShare(3, 2)).toBe(150);
    expect(usedShare(1, null)).toBeNull();
    expect(usedShare(1, 0)).toBeNull();
  });
});
