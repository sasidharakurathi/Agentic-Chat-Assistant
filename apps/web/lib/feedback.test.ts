import { describe, expect, it } from "vitest";

import { solvedWords, upShare } from "./feedback";

describe("feedback in words (Phase 8.8)", () => {
  it("says the solved rate with how many answered", () => {
    expect(solvedWords(3, 1, 20)).toBe(
      "75% said it solved it: 3 of 4 who answered, in 20 conversations.",
    );
    expect(solvedWords(0, 0, 0)).toBe("No conversations yet.");
    expect(solvedWords(0, 0, 1)).toBe("Nobody has said yet, in 1 conversation.");
  });

  it("shares thumbs up only when there are some", () => {
    expect(upShare(3, 1)).toBe(75);
    expect(upShare(0, 0)).toBeNull();
  });
});
