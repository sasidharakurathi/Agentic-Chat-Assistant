import { describe, expect, it } from "vitest";

import type { MessageBlock } from "./api";
import { guardrailTitle, splitBlocks } from "./message-blocks";

const call = { id: "c1", name: "mcp__caps__sql_query", input: {} };
const legacy = { id: "c0", name: "mcp__caps__kb_search", input: {} }; // before 2.9: no type
const cite = { type: "citation", marker: 1 };
const guard = { type: "guardrail", check: "pii", where: "tool_input", detail: "email" };

describe("splitBlocks", () => {
  it("keeps untyped (old) blocks as tool calls and sorts the rest by kind", () => {
    const blocks = [
      legacy,
      { type: "tool_call", ...call },
      cite,
      guard,
    ] as unknown as MessageBlock[];
    const { tools, cites, guards } = splitBlocks(blocks);
    expect(tools.map((t) => t.id)).toEqual(["c0", "c1"]);
    expect(cites).toHaveLength(1);
    expect(guards).toEqual([guard]);
  });

  it("never shows a guardrail finding as a tool card", () => {
    expect(splitBlocks([guard] as unknown as MessageBlock[]).tools).toEqual([]);
  });

  it("handles no blocks", () => {
    expect(splitBlocks(undefined)).toEqual({ tools: [], cites: [], guards: [] });
  });
});

describe("guardrailTitle", () => {
  it("names each check, and where an injection was found", () => {
    expect(guardrailTitle({ check: "injection", where: "user_message" })).toBe(
      "Instruction override attempt",
    );
    expect(guardrailTitle({ check: "injection", where: "tool_result" })).toBe(
      "Instructions found in a tool result",
    );
    expect(guardrailTitle({ check: "exfiltration", where: "tool_input" })).toBe(
      "Credential kept from leaving",
    );
  });
});
