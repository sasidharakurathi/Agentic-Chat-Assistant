import { describe, expect, it } from "vitest";

import type { Citation } from "@/lib/api";

import { citeMarker, withCitationLinks } from "./markdown-citations";

const cite = (marker: number, spans: [number, number][]): Citation =>
  ({ marker, spans }) as unknown as Citation;

describe("withCitationLinks", () => {
  it("turns each reported span into a citation link, keeping the rest", () => {
    const text = "Refunds take **5 days** [1]. Store credit is instant [2].";
    const out = withCitationLinks(text, [
      cite(1, [[text.indexOf("[1]"), text.indexOf("[1]") + 3]]),
      cite(2, [[text.indexOf("[2]"), text.indexOf("[2]") + 3]]),
    ]);
    expect(out).toBe("Refunds take **5 days** [1](#cite-1). Store credit is instant [2](#cite-2).");
  });

  it("only converts what the backend reported, never other brackets", () => {
    // `items[1]` is indexing: the backend reports no span for it.
    const text = "Use `items[1]` here [1].";
    const at = text.lastIndexOf("[1]");
    expect(withCitationLinks(text, [cite(1, [[at, at + 3]])])).toBe(
      "Use `items[1]` here [1](#cite-1).",
    );
  });

  it("handles a marker cited twice and ignores bad spans", () => {
    const text = "[1] and again [1]";
    const out = withCitationLinks(text, [
      cite(1, [
        [0, 3],
        [14, 17],
        [15, 99],
      ]),
    ]);
    expect(out).toBe("[1](#cite-1) and again [1](#cite-1)");
  });
});

describe("citeMarker", () => {
  it("reads citation links and nothing else", () => {
    expect(citeMarker("#cite-3")).toBe(3);
    expect(citeMarker("https://example.com")).toBeNull();
    expect(citeMarker("#cite-x")).toBeNull();
    expect(citeMarker(undefined)).toBeNull();
  });
});
