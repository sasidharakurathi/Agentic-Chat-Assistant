import { describe, expect, it } from "vitest";

import { subagentLabel, toolLabel, toolShortName } from "./tool-label";

describe("toolLabel", () => {
  it("names the server for registered MCP tools only", () => {
    expect(toolLabel("mcp__caps__sql_query")).toBe("sql_query");
    expect(toolLabel("mcp__github__search_issues")).toBe("github: search_issues");
    expect(toolLabel("mcp__my-tools__do_it")).toBe("my-tools: do_it");
    expect(toolLabel("WebSearch")).toBe("WebSearch");
  });

  it("gives the bare name for behaviour that depends on the tool", () => {
    expect(toolShortName("mcp__caps__kb_search")).toBe("kb_search");
    expect(toolShortName("mcp__echo__echo")).toBe("echo");
  });
});

describe("subagentLabel", () => {
  it("names the subagent a delegation went to", () => {
    expect(subagentLabel("Agent", { subagent_type: "sql", prompt: "x" })).toBe("sql subagent");
    expect(subagentLabel("Agent", { prompt: "x" })).toBeNull();
    expect(subagentLabel("mcp__caps__sql_query", { subagent_type: "sql" })).toBeNull();
  });
});
