import { describe, expect, it } from "vitest";

import { safeNext } from "./safe-next";

describe("safeNext", () => {
  it.each([
    ["/assistants", "/assistants"],
    ["/invite/abc?x=1#top", "/invite/abc?x=1#top"],
    ["/assistants/../settings", "/settings"],
  ])("keeps a same-site path %j", (next, expected) => {
    expect(safeNext(next)).toBe(expected);
  });

  it.each([
    null,
    undefined,
    "",
    "assistants",
    "https://evil.example/",
    "//evil.example",
    // A backslash is a slash to the browser: these are `//evil.example`.
    String.raw`/\evil.example`,
    String.raw`\\evil.example`,
    String.raw`/\/evil.example`,
    // The browser strips tabs and newlines before parsing, so these become
    // `//evil.example`. They passed the old string checks.
    "/\t/evil.example",
    "/\n/evil.example",
    "/\r\n/evil.example",
    "javascript:alert(1)",
  ])("refuses %j", (next) => {
    expect(safeNext(next)).toBeNull();
  });
});
