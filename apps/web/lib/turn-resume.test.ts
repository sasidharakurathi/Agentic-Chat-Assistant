import { describe, expect, it } from "vitest";

import { ApiError } from "./api";
import {
  giveBack,
  keptAlready,
  limitBack,
  pause,
  retryAfterOf,
  unreachable,
  whenBack,
} from "./turn-resume";

describe("a conversation on a weak connection (Phase 8.6)", () => {
  it("tells a dropped connection from a refusal", () => {
    expect(unreachable(new TypeError("Failed to fetch"))).toBe(true);
    expect(unreachable(new ApiError(0, { error: { code: "offline", message: "x" } }))).toBe(true);
    expect(
      unreachable(new ApiError(409, { error: { code: "turn_in_progress", message: "x" } })),
    ).toBe(false);
  });

  it("gives the text back unless something new was typed", () => {
    expect(giveBack("", "my question")).toBe("my question");
    expect(giveBack("  ", "my question")).toBe("my question");
    expect(giveBack("a newer one", "my question")).toBe("a newer one");
  });

  it("knows a message got there when it is the newest one you wrote", () => {
    const thread = [
      { role: "user", content: "first" },
      { role: "assistant", content: "an answer" },
      { role: "user", content: "my question " },
    ];
    expect(keptAlready(thread, "my question")).toBe(true);
    expect(keptAlready(thread, "first")).toBe(false);
    expect(keptAlready([], "my question")).toBe(false);
    expect(keptAlready([], undefined)).toBe(true);
  });

  it("knows a message got there when the server masked a password in it (Phase 9.6)", () => {
    const thread = [
      { role: "assistant", content: "How can I help?" },
      { role: "user", content: "I can't sign in, password: <redacted>! What now?" },
    ];
    expect(keptAlready(thread, "I can't sign in, password: Hunter2!! What now?")).toBe(true);
    expect(keptAlready(thread, "  I can't sign in, password: Hunter2!! What now? ")).toBe(true);
    // A different message is still not the one kept: the box is refilled.
    expect(keptAlready(thread, "I can't sign in, what now?")).toBe(false);
    expect(keptAlready(thread, "I can't sign in, password: ! What now?")).toBe(false);
  });

  it("says when a used-up limit is back, the builder budget too", () => {
    const now = new Date(2026, 9, 9, 14, 0);
    const at = new Date(2026, 9, 9, 17, 30).toISOString();
    for (const code of ["budget_exceeded", "person_limit", "builder_budget"]) {
      expect(limitBack(code, at, now)).toMatch(/^It will be back at /);
    }
    expect(limitBack("builder_budget", null, now)).toBe("");
    expect(limitBack("rate_limited", at, now)).toBe("");
  });

  it("reads how long to wait", () => {
    const limited = new ApiError(429, {
      error: { code: "rate_limited", message: "x", details: { retry_after_s: 7 } },
    });
    expect(retryAfterOf(limited)).toBe(7);
    expect(retryAfterOf({ retry_after_s: 9.2 })).toBe(10);
    expect(retryAfterOf({ retry_after_s: null })).toBeNull();
    expect(retryAfterOf(new ApiError(409, { error: { code: "x", message: "x" } }))).toBeNull();
  });

  it("says when a limit starts again in the reader's own time", () => {
    const now = new Date(2026, 9, 9, 14, 0);
    expect(whenBack(null, now)).toBe("");
    expect(whenBack("not a date", now)).toBe("");
    expect(whenBack(new Date(2026, 9, 9, 17, 30).toISOString(), now, "en-GB")).toBe(
      "It will be back at 17:30.",
    );
    expect(whenBack(new Date(2026, 9, 10, 5, 30).toISOString(), now, "en-GB")).toBe(
      "It will be back tomorrow at 05:30.",
    );
    expect(whenBack(new Date(2026, 10, 1, 5, 30).toISOString(), now, "en-GB")).toBe(
      "It will be back on 1 November.",
    );
  });

  it("stops waiting when the conversation is left", async () => {
    const left = new AbortController();
    const waited = pause(60_000, left.signal);
    left.abort();
    await expect(waited).resolves.toBeUndefined();
  });
});
