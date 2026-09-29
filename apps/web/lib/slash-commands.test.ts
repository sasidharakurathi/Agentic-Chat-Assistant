import { describe, expect, it } from "vitest";

import { conversationMarkdown, parseInput, suggestCommands } from "./slash-commands";

describe("parseInput", () => {
  it("treats ordinary text as a message", () => {
    expect(parseInput("  hello  ")).toEqual({ kind: "message", text: "hello" });
  });

  it("reads a command and its argument", () => {
    expect(parseInput("/rename  Refund questions ")).toEqual({
      kind: "command",
      name: "rename",
      arg: "Refund questions",
    });
    expect(parseInput("/NEW")).toEqual({ kind: "command", name: "new", arg: "" });
  });

  it("reports an unknown command instead of sending it to the model", () => {
    expect(parseInput("/frobnicate now")).toEqual({ kind: "unknown", name: "frobnicate" });
  });

  it("sends // as a message that starts with a slash", () => {
    expect(parseInput("//etc/hosts is a file")).toEqual({
      kind: "message",
      text: "/etc/hosts is a file",
    });
  });
});

describe("suggestCommands", () => {
  it("filters by prefix while a command is being typed", () => {
    expect(suggestCommands("/").length).toBeGreaterThan(5);
    expect(suggestCommands("/re").map((c) => c.name)).toEqual(["rename", "retry"]);
  });

  it("offers nothing for messages, escapes or once an argument starts", () => {
    expect(suggestCommands("hello")).toEqual([]);
    expect(suggestCommands("//x")).toEqual([]);
    expect(suggestCommands("/rename x")).toEqual([]);
  });
});

describe("conversationMarkdown", () => {
  it("writes a heading per message, in order", () => {
    const md = conversationMarkdown("Maths", [
      { role: "user", content: "calculate 21 + 21" },
      { role: "assistant", content: "42 " },
    ]);
    expect(md).toBe("# Maths\n\n## You\n\ncalculate 21 + 21\n\n## Assistant\n\n42\n");
  });
});
