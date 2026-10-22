import { describe, expect, it } from "vitest";

import { erasureWords, retentionWords } from "./retention";

describe("keeping and erasing in words (Phase 8.10)", () => {
  it("says what the setting means, and warns below a year", () => {
    expect(retentionWords(365)).toEqual({
      meaning: "Conversations with no message for 1 year are erased for good, every night.",
      warning: null,
    });
    expect(retentionWords(730).meaning).toContain("2 years");
    const short = retentionWords(90);
    expect(short.meaning).toContain("90 days");
    expect(short.warning).toContain("DPDP");
  });

  it("puts an erasure in a line", () => {
    expect(erasureWords({ kind: "retention", conversations: 12, memories: 0 })).toBe(
      "Retention: 12 conversations",
    );
    expect(
      erasureWords({
        kind: "person",
        user_email: "pat@acme.example",
        conversations: 1,
        memories: 2,
      }),
    ).toBe("Erased pat@acme.example's data: 1 conversation, 2 memory notes");
    expect(erasureWords({ kind: "person", conversations: 0, memories: 0 })).toBe(
      "Erased a former member's data: 0 conversations",
    );
  });
});
