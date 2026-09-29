import { describe, expect, it } from "vitest";

import { appendSubagentText, nestCalls, permissionText, type ToolCallView } from "./tool-tree";

const call = (id: string, parent_id?: string): ToolCallView => ({
  id,
  name: id,
  input: {},
  parent_id,
});

describe("nestCalls", () => {
  it("puts a subagent's calls under its delegation call", () => {
    const { top, children } = nestCalls([
      call("task"),
      call("kb1", "task"),
      call("calc"),
      call("kb2", "task"),
    ]);
    expect(top.map((c) => c.id)).toEqual(["task", "calc"]);
    expect(children.get("task")?.map((c) => c.id)).toEqual(["kb1", "kb2"]);
  });

  it("keeps a call whose parent is missing at the top level", () => {
    const { top } = nestCalls([call("kb1", "gone")]);
    expect(top.map((c) => c.id)).toEqual(["kb1"]);
  });
});

describe("appendSubagentText", () => {
  it("accumulates on the delegation call only", () => {
    let calls = [call("task"), call("other")];
    calls = appendSubagentText(calls, "task", "Searching. ");
    calls = appendSubagentText(calls, "task", "Done.");
    expect(calls[0].subagent_text).toBe("Searching. Done.");
    expect(calls[1].subagent_text).toBeUndefined();
  });
});

describe("permissionText", () => {
  it("says how a call was permitted, in words", () => {
    expect(permissionText("auto")).toBe("Ran without asking");
    expect(permissionText("refused")).toBe("Not allowed by this assistant's rules");
    expect(permissionText(null)).toBeNull();
  });
});
