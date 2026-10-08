import { describe, expect, it } from "vitest";

import { externalHost, imageNote, textShowsAddress } from "./safe-links";

describe("externalHost", () => {
  it("names the host of an address on another site", () => {
    expect(externalHost("https://evil.example/?d=secret")).toBe("evil.example");
    expect(externalHost("http://docs.example:8080/a")).toBe("docs.example:8080");
  });

  it("is null for links that stay on this site", () => {
    expect(externalHost("#cite-1")).toBeNull();
    expect(externalHost("/assistants/1/chat")).toBeNull();
    expect(externalHost("https://studio.example/x", "https://studio.example")).toBeNull();
  });

  it("is null for anything that is not a web address", () => {
    expect(externalHost("mailto:it@example.com")).toBeNull();
    expect(externalHost(undefined)).toBeNull();
    expect(externalHost("")).toBeNull();
  });
});

describe("textShowsAddress", () => {
  it("knows when the text already is the address", () => {
    expect(textShowsAddress("https://example.com/policy", "https://example.com/policy")).toBe(true);
    expect(textShowsAddress("example.com", "https://example.com/")).toBe(true);
  });

  it("knows when the text hides where the link goes", () => {
    expect(textShowsAddress("Refund policy", "https://evil.example/?d=1")).toBe(false);
  });
});

describe("imageNote", () => {
  it("says an image was left out and where it would have come from", () => {
    expect(imageNote("chart", "https://evil.example/c.png?d=secret")).toBe(
      "Image not shown: chart (evil.example)",
    );
    expect(imageNote("", "https://evil.example/x")).toBe("Image not shown (evil.example)");
    expect(imageNote(undefined, undefined)).toBe("Image not shown");
  });
});
