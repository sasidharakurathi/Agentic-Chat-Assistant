import { describe, expect, it } from "vitest";

import type { Graph, GraphFix } from "./api";
import { applyFix, fixFocus } from "./graph-fixes";

const op = (o: Partial<GraphFix["ops"][number]>): GraphFix["ops"][number] => ({
  op: "add_edge",
  source: null,
  target: null,
  node_id: null,
  node_type: null,
  data: null,
  position: null,
  ...o,
});

const graph: Graph = {
  schema_version: 1,
  nodes: [
    { id: "input", type: "input", position: { x: 0, y: 0 }, data: {} },
    { id: "agent", type: "agent", position: { x: 0, y: 0 }, data: {} },
    { id: "db", type: "database", position: { x: 0, y: 0 }, data: { expose_write: true, a: 1 } },
  ],
  edges: [
    { source: "input", target: "agent" },
    { source: "db", target: "agent" },
  ],
};

describe("applyFix", () => {
  it("adds an edge once, however often it is asked", () => {
    const fix = {
      label: "x",
      ops: [op({ source: "db", target: "agent" }), op({ source: "input", target: "db" })],
    };
    expect(applyFix(graph, fix).edges).toHaveLength(3);
  });

  it("removes an edge", () => {
    const fix = { label: "x", ops: [op({ op: "remove_edge", source: "db", target: "agent" })] };
    expect(applyFix(graph, fix).edges).toEqual([{ source: "input", target: "agent" }]);
  });

  it("removes a node and every edge touching it", () => {
    const fix = { label: "x", ops: [op({ op: "remove_node", node_id: "db" })] };
    const after = applyFix(graph, fix);
    expect(after.nodes.map((n) => n.id)).toEqual(["input", "agent"]);
    expect(after.edges).toEqual([{ source: "input", target: "agent" }]);
  });

  it("merges a patch into the node's data", () => {
    const fix = {
      label: "x",
      ops: [op({ op: "patch_node", node_id: "db", data: { expose_write: false } })],
    };
    expect(applyFix(graph, fix).nodes[2].data).toEqual({ expose_write: false, a: 1 });
    expect(fixFocus(fix)).toBe("db");
  });

  it("adds a node where it is told", () => {
    const fix = {
      label: "Add a memory node",
      ops: [
        op({
          op: "add_node",
          node_id: "memory",
          node_type: "memory",
          data: {},
          position: { x: 5, y: 6 },
        }),
        op({ source: "memory", target: "agent" }),
      ],
    };
    const after = applyFix(graph, fix);
    expect(after.nodes.at(-1)).toEqual({
      id: "memory",
      type: "memory",
      position: { x: 5, y: 6 },
      data: {},
    });
    expect(after.edges.at(-1)).toEqual({ source: "memory", target: "agent" });
    expect(fixFocus(fix)).toBe("memory");
  });

  it("leaves the graph it was given alone", () => {
    const before = JSON.stringify(graph);
    applyFix(graph, { label: "x", ops: [op({ op: "remove_node", node_id: "db" })] });
    expect(JSON.stringify(graph)).toBe(before);
  });
});
