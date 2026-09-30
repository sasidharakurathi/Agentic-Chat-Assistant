import { describe, expect, it } from "vitest";

import { subagentLabel, toolLabel, toolNodeType, toolSentence, toolShortName } from "./tool-label";

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

describe("toolNodeType", () => {
  it("files each call under the capability it belongs to", () => {
    expect(toolNodeType("mcp__caps__kb_search")).toBe("knowledge_base");
    expect(toolNodeType("mcp__caps__sql_query")).toBe("database");
    expect(toolNodeType("mcp__caps__mongo_find")).toBe("database");
    expect(toolNodeType("mcp__caps__memory")).toBe("memory");
    expect(toolNodeType("mcp__caps__http_request")).toBe("tool");
    expect(toolNodeType("mcp__github__search")).toBe("mcp_server");
    expect(toolNodeType("Agent")).toBe("subagent");
    expect(toolNodeType("WebSearch")).toBe("tool");
    expect(toolNodeType("something_new")).toBe("tool");
  });
});

describe("toolSentence", () => {
  it("says what a call did in plain words", () => {
    expect(toolSentence("mcp__caps__sql_query", { sql: "select 1" })).toBe("Ran a SQL query");
    expect(toolSentence("mcp__caps__sql_introspect", { table: "tickets" })).toBe(
      "Looked up the columns of tickets",
    );
    expect(toolSentence("mcp__caps__kb_search", { query: "refund  policy" })).toBe(
      'Searched the knowledge base for "refund policy"',
    );
    expect(
      toolSentence("mcp__caps__http_request", { method: "post", url: "https://api.example.com/x" }),
    ).toBe("Sent a POST request to api.example.com");
    expect(toolSentence("mcp__caps__http_request", { url: "not a url" })).toBe(
      "Sent a web request",
    );
    expect(toolSentence("mcp__github__search", {})).toBe("Used search on github");
    expect(toolSentence("Agent", { subagent_type: "sql" })).toBe("Asked the sql subagent");
    expect(toolSentence("WebSearch", null)).toBe("Searched the web");
    expect(toolSentence("mcp__caps__memory", { command: "view" })).toBe("Read its memory");
  });

  it("shortens a long query", () => {
    const s = toolSentence("mcp__caps__kb_search", { query: "x".repeat(200) });
    expect(s.length).toBeLessThan(100);
    expect(s.endsWith('…"')).toBe(true);
  });
});
