import { describe, expect, it } from "vitest";

import { contactLink, noticeBlocks } from "./notice-text";

describe("the privacy notice's text (Phase 8.7)", () => {
  it("reads headings, paragraphs and lists from plain text", () => {
    const body = [
      "Please read this first.",
      "",
      "What is kept",
      "- Your conversations.",
      "- What you use.",
      "",
      "Why you're asked: so the rules apply.",
    ].join("\n");
    expect(noticeBlocks(body)).toEqual([
      { kind: "paragraph", text: "Please read this first." },
      { kind: "heading", text: "What is kept" },
      { kind: "list", items: ["Your conversations.", "What you use."] },
      { kind: "paragraph", text: "Why you're asked: so the rules apply." },
    ]);
  });

  it("keeps a sentence before a list as a sentence", () => {
    expect(noticeBlocks("Before you start:\n- one\n- two")).toEqual([
      { kind: "paragraph", text: "Before you start:" },
      { kind: "list", items: ["one", "two"] },
    ]);
  });

  it("joins a paragraph's lines and ignores Windows line ends", () => {
    expect(noticeBlocks("One line\r\nand the next.")).toEqual([
      { kind: "paragraph", text: "One line and the next." },
    ]);
  });

  it("writes to the contact only when it is an address", () => {
    expect(contactLink("privacy@acme.example", "About my data")).toBe(
      "mailto:privacy@acme.example?subject=About%20my%20data",
    );
    expect(contactLink("", "x")).toBeNull();
    expect(contactLink("not an address", "x")).toBeNull();
  });
});
