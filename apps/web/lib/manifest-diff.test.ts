import { describe, expect, it } from "vitest";

import { compareManifests, type Manifest } from "./manifest-diff";

const base: Manifest = {
  knowledge: {
    sources: [{ id: "s1", name: "Refund policy" }],
    documents: [{ id: "d1", source_id: "s1" }],
  },
  databases: [{ id: "c1", name: "Tickets", fingerprint: "f1" }],
  mcp_servers: [{ id: "m1", name: "shop", fingerprint: "g1", tools: { lookup: "t1" } }],
};

describe("what changed beyond the settings (Phase 7b.1)", () => {
  it("nothing, when the two match", () => {
    expect(compareManifests(base, structuredClone(base))).toEqual([]);
  });

  it("a new copy of a source's documents", () => {
    const to = structuredClone(base);
    to.knowledge!.documents = [{ id: "d2", source_id: "s1" }];
    expect(compareManifests(base, to).map((c) => c.text)).toEqual([
      'Knowledge source "Refund policy" has new content',
    ]);
  });

  it("a connection that changed, and a tool that now says read-only", () => {
    const to = structuredClone(base);
    to.databases![0].fingerprint = "f2";
    to.mcp_servers![0].tools = { lookup: "t2", refund: "t3" };
    expect(compareManifests(base, to).map((c) => [c.kind, c.change])).toEqual([
      ["database", "changed"],
      ["mcp_tool", "changed"],
      ["mcp_tool", "added"],
    ]);
  });

  it("sources, connections and servers added and removed", () => {
    const to: Manifest = {
      knowledge: { sources: [{ id: "s2", name: "FAQ" }], documents: [] },
      databases: [],
      mcp_servers: [],
    };
    expect(compareManifests(base, to).map((c) => c.text)).toEqual([
      'Knowledge source "FAQ" added',
      'Knowledge source "Refund policy" removed',
      'Database connection "Tickets" removed',
      'MCP server "shop" removed',
    ]);
  });

  it("an HTTP connection added, changed and removed (Phase 9.3)", () => {
    const from: Manifest = {
      ...base,
      http_connections: [
        { id: "h1", name: "Tickets", fingerprint: "a" },
        { id: "h2", name: "HR", fingerprint: "b" },
      ],
    };
    const to: Manifest = {
      ...base,
      http_connections: [
        { id: "h1", name: "Tickets", fingerprint: "a2" },
        { id: "h3", name: "Wiki", fingerprint: "c" },
      ],
    };
    expect(compareManifests(from, to).map((c) => [c.kind, c.change])).toEqual([
      ["http_connection", "changed"],
      ["http_connection", "added"],
      ["http_connection", "removed"],
    ]);
  });

  it("says nothing about a version with no manifest", () => {
    expect(compareManifests(null, base)).toEqual([]);
    expect(compareManifests(base, undefined)).toEqual([]);
  });
});
