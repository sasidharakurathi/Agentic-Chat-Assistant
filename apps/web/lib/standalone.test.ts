import { afterEach, describe, expect, it, vi } from "vitest";

import { isInstalledApp, showCode } from "./standalone";

describe("the installed app (Phase 8.6)", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("shows a device code in two groups of four", () => {
    expect(showCode("ABCDEFGH")).toBe("ABCD-EFGH");
    expect(showCode("abcd efgh")).toBe("ABCD-EFGH");
    expect(showCode("ABC")).toBe("ABC");
  });

  it("knows when it runs from the home screen", () => {
    expect(isInstalledApp()).toBe(false);
    vi.stubGlobal("window", { matchMedia: () => ({ matches: false }) });
    vi.stubGlobal("navigator", { standalone: true });
    expect(isInstalledApp()).toBe(true);
    vi.stubGlobal("navigator", {});
    vi.stubGlobal("window", { matchMedia: () => ({ matches: true }) });
    expect(isInstalledApp()).toBe(true);
  });
});
