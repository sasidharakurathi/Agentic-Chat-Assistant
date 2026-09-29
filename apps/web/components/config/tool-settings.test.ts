import { describe, expect, it } from "vitest";

import { parseDomains, stricter } from "./tool-settings";

describe("parseDomains", () => {
  it("normalizes the way the server does", () => {
    expect(parseDomains("https://Example.com/path, *.docs.python.org  api.test:8443")).toEqual([
      "example.com",
      "docs.python.org",
      "api.test",
    ]);
  });

  it("drops blanks and duplicates", () => {
    expect(parseDomains(" , example.com,EXAMPLE.com ,, ")).toEqual(["example.com"]);
    expect(parseDomains("")).toEqual([]);
  });
});

describe("stricter", () => {
  it("lets either side insist, and deny beats everything", () => {
    expect(stricter("auto", "require")).toBe("require");
    expect(stricter("require", "auto")).toBe("require");
    expect(stricter("auto", "auto")).toBe("auto");
    expect(stricter("require", "deny")).toBe("deny");
  });
});
