import { afterEach, describe, expect, it, vi } from "vitest";

import { OFFLINE_HELP_KEY, readOfflineHelp, rememberOfflineHelp } from "./offline-help";

describe("the offline page's steps (Phase 8.6)", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("keeps the organisation's steps for when the server can't be reached", () => {
    const stored = new Map<string, string>();
    vi.stubGlobal("window", {
      localStorage: { setItem: (k: string, v: string) => stored.set(k, v) },
    });
    rememberOfflineHelp({ organisation: "Acme", offline_help: "Turn on Tailscale." });
    expect(readOfflineHelp(stored.get(OFFLINE_HELP_KEY) ?? null)).toEqual({
      organisation: "Acme",
      steps: "Turn on Tailscale.",
    });
  });

  it("carries on when the browser won't store it", () => {
    vi.stubGlobal("window", {
      localStorage: {
        setItem: () => {
          throw new Error("QuotaExceededError");
        },
      },
    });
    expect(() => rememberOfflineHelp({ organisation: "Acme" })).not.toThrow();
  });

  it("says nothing it doesn't have", () => {
    expect(readOfflineHelp(null)).toEqual({ organisation: "", steps: "" });
    expect(readOfflineHelp("not json")).toEqual({ organisation: "", steps: "" });
    expect(readOfflineHelp('{"organisation": 5}')).toEqual({ organisation: "", steps: "" });
  });
});
