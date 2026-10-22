import { describe, expect, it } from "vitest";

import { erasureDetailWords } from "./retention";

describe("what went with an erasure (Phase 10.5)", () => {
  it("counts each kind, and says nothing for an old record", () => {
    expect(
      erasureDetailWords({ messages: 12, traces: 3, summaries: 1, share_links: 0, tool_calls: 1 }),
    ).toBe("With them: 12 messages, 3 run traces, 1 tool call, 1 summary");
    expect(erasureDetailWords({})).toBeNull();
    expect(erasureDetailWords(undefined)).toBeNull();
  });
});
