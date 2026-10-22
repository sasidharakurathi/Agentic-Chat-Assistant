import { describe, expect, it } from "vitest";

import { googleError, googleStartUrl, parseHandoff } from "./google";

describe("Google sign-in in the web app (Phase 8.3)", () => {
  it("starts at the API, with the page to come back to", () => {
    const url = new URL(googleStartUrl({ next: "/chat", invite: "abc" }));
    expect(url.pathname).toBe("/api/v1/auth/google/start");
    expect(url.searchParams.get("next")).toBe("/chat");
    expect(url.searchParams.get("invite")).toBe("abc");
  });

  it("never carries another site as the page to come back to", () => {
    const url = new URL(googleStartUrl({ next: "//evil.example" }));
    expect(url.searchParams.get("next")).toBeNull();
  });

  it("reads the code and the next page from the fragment", () => {
    expect(parseHandoff("#code=c0de&next=%2Fchat")).toEqual({ code: "c0de", next: "/chat" });
    expect(parseHandoff("#code=c0de&next=https%3A%2F%2Fevil.example")).toEqual({
      code: "c0de",
      next: null,
    });
  });

  it("says why, in words, and stays quiet on success", () => {
    expect(googleError("password_account")).toMatch(/connect Google in Your account/);
    expect(googleError("something_new")).toMatch(/didn't work/);
    expect(googleError("linked")).toBeNull();
    expect(googleError(null)).toBeNull();
  });
});
