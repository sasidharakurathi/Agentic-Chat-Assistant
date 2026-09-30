import { describe, expect, it } from "vitest";

import { ApiError } from "@/lib/api";

import { asSentence, failureText } from "./error-text";

describe("asSentence", () => {
  it("capitalises and ends with a full stop once", () => {
    expect(asSentence("maximum agent turns reached")).toBe("Maximum agent turns reached.");
    expect(asSentence("Already done.")).toBe("Already done.");
    expect(asSentence("  ")).toBe("");
  });
});

describe("failureText", () => {
  it("says what failed, why, and what to do", () => {
    const err = new ApiError(404, {
      error: { code: "not_found", message: "conversation not found" },
    });
    expect(failureText("Couldn't rename the conversation.", err)).toBe(
      "Couldn't rename the conversation. Conversation not found. Try again in a moment.",
    );
    expect(failureText("Couldn't rename the conversation.", new TypeError("Failed to fetch"))).toBe(
      "Couldn't rename the conversation. The server couldn't be reached. Check your connection, then try again.",
    );
  });
});
