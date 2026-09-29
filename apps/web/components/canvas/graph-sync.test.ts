import { describe, expect, it } from "vitest";

import type { Graph, GraphNode, ValidationResult } from "@/lib/api";

import {
  addEdge,
  edgeKey,
  indexIssues,
  mergeFlowNodes,
  nextNodeId,
  removeNodes,
  STRUCTURAL_NODE_TYPES,
  tidyLayout,
  toFlow,
} from "./graph-sync";

const node = (id: string, type: string, x = 0, y = 0): GraphNode => ({
  id,
  type,
  position: { x, y },
  data: {},
});

/** The shape every assistant starts from, plus two capabilities. */
function pipeline(): Graph {
  return {
    schema_version: 1,
    nodes: [
      node("in", "input", 5, 5),
      node("guard", "guardrail", 17, -3),
      node("mem", "memory", 900, 900),
      node("kb", "knowledge_base", -40, 12),
      node("calc", "tool", 3, 400),
      node("ag", "agent", 1, 1),
      node("out", "output", 2, 2),
    ],
    edges: [
      { source: "in", target: "guard" },
      { source: "guard", target: "ag" },
      { source: "kb", target: "ag" },
      { source: "calc", target: "ag" },
      { source: "mem", target: "ag" },
      { source: "ag", target: "out" },
    ],
  };
}

describe("removeNodes", () => {
  it("drops a node and every edge touching it", () => {
    const g = removeNodes(pipeline(), ["kb"]);
    expect(g.nodes.map((n) => n.id)).not.toContain("kb");
    expect(g.edges.some((e) => e.source === "kb" || e.target === "kb")).toBe(false);
    expect(g.edges).toHaveLength(5);
  });

  it.each([...STRUCTURAL_NODE_TYPES])("never removes the %s node", (type) => {
    const g = pipeline();
    const id = g.nodes.find((n) => n.type === type)!.id;
    // Alone, or in a multi-select with a deletable node (the Delete key's case).
    expect(removeNodes(g, [id])).toEqual(g);
    const after = removeNodes(g, [id, "calc"]);
    expect(after.nodes.map((n) => n.id)).toContain(id);
    expect(after.nodes.map((n) => n.id)).not.toContain("calc");
    // Only calc's own wire goes.
    expect(after.edges).toEqual(g.edges.filter((e) => e.source !== "calc"));
  });

  it("does not modify the graph it was given", () => {
    const g = pipeline();
    const before = JSON.stringify(g);
    removeNodes(g, ["kb"]);
    expect(JSON.stringify(g)).toBe(before);
  });
});

describe("toFlow", () => {
  it("marks structural nodes as not deletable on the canvas", () => {
    const { nodes } = toFlow(pipeline());
    const deletable = Object.fromEntries(nodes.map((n) => [n.id, n.deletable]));
    expect(deletable).toEqual({
      in: false,
      guard: false,
      mem: false,
      kb: true,
      calc: true,
      ag: false,
      out: false,
    });
  });

  it("paints an edge that has an error", () => {
    const validation: ValidationResult = {
      errors: [{ code: "illegal_edge", message: "no", node_id: null, edge: ["kb", "ag"] }],
      warnings: [],
    };
    const { edges } = toFlow(pipeline(), indexIssues(validation));
    const bad = edges.find((e) => e.source === "kb")!;
    expect(bad.label).toBe("no");
    expect(edges.find((e) => e.source === "calc")!.label).toBeUndefined();
  });
});

describe("indexIssues", () => {
  it("files errors and warnings under their node, and edge errors under the edge", () => {
    const issues = indexIssues({
      errors: [
        { code: "a", message: "kb broken", node_id: "kb", edge: null },
        { code: "b", message: "kb twice", node_id: "kb", edge: null },
        { code: "c", message: "bad wire", node_id: null, edge: ["calc", "ag"] },
      ],
      warnings: [
        { code: "d", message: "defaults in use", node_id: "guard", edge: null },
        { code: "e", message: "graph-wide", node_id: null, edge: null },
      ],
    });
    expect(issues.nodes.get("kb")).toEqual({ errors: ["kb broken", "kb twice"], warnings: [] });
    expect(issues.nodes.get("guard")).toEqual({ errors: [], warnings: ["defaults in use"] });
    expect(issues.edges.get(edgeKey("calc", "ag"))).toEqual(["bad wire"]);
    expect([...issues.nodes.keys()].sort()).toEqual(["guard", "kb"]);
  });
});

describe("tidyLayout", () => {
  it("puts each type in its column, left to right in reading order", () => {
    const x = Object.fromEntries(tidyLayout(pipeline()).nodes.map((n) => [n.id, n.position.x]));
    expect(x.in).toBeLessThan(x.guard);
    expect(x.guard).toBeLessThan(x.kb);
    expect(x.kb).toBe(x.calc);
    expect(x.kb).toBeLessThan(x.ag);
    expect(x.ag).toBeLessThan(x.out);
  });

  it("stacks a column without overlaps, centred on the agent's row", () => {
    const tidy = tidyLayout(pipeline());
    const column = tidy.nodes.filter(
      (n) => n.position.x === tidy.nodes.find((m) => m.id === "kb")!.position.x,
    );
    const ys = column.map((n) => n.position.y).sort((a, b) => a - b);
    expect(new Set(ys).size).toBe(ys.length);
    expect(ys[0] + ys[ys.length - 1]).toBe(0);
    expect(tidy.nodes.find((n) => n.id === "ag")!.position.y).toBe(0);
  });

  it("is idempotent and changes nothing but positions", () => {
    const once = tidyLayout(pipeline());
    expect(tidyLayout(once)).toEqual(once);
    const strip = (g: Graph) => ({ ...g, nodes: g.nodes.map((n) => ({ ...n, position: null })) });
    expect(strip(once)).toEqual(strip(pipeline()));
  });

  it("does not depend on where the nodes were", () => {
    const shuffled = pipeline();
    shuffled.nodes = [...shuffled.nodes].reverse().map((n, i) => ({
      ...n,
      position: { x: i * 37, y: -i * 11 },
    }));
    const pos = (g: Graph) => Object.fromEntries(g.nodes.map((n) => [n.id, n.position]));
    expect(pos(tidyLayout(shuffled))).toEqual(pos(tidyLayout(pipeline())));
  });
});

describe("small helpers", () => {
  it("nextNodeId skips ids in use", () => {
    const g = pipeline();
    expect(nextNodeId(g, "sub")).toBe("sub");
    expect(nextNodeId(g, "kb")).toBe("kb-2");
    g.nodes.push(node("kb-2", "knowledge_base"));
    expect(nextNodeId(g, "kb")).toBe("kb-3");
  });

  it("addEdge never duplicates a wire", () => {
    const g = pipeline();
    expect(addEdge(g, "kb", "ag")).toBe(g);
    expect(addEdge(g, "kb", "out").edges).toHaveLength(g.edges.length + 1);
  });
});

describe("mergeFlowNodes", () => {
  it("keeps each surviving node's measured size across a graph update", () => {
    const before = toFlow(pipeline()).nodes.map((n) => ({
      ...n,
      measured: { width: 120, height: 40 },
    }));
    const after = toFlow(removeNodes(pipeline(), ["kb"])).nodes;
    const merged = mergeFlowNodes(before, after, "ag");
    expect(merged.map((n) => n.id)).not.toContain("kb");
    expect(merged.every((n) => n.measured?.width === 120)).toBe(true);
    expect(merged.filter((n) => n.selected).map((n) => n.id)).toEqual(["ag"]);
  });

  it("leaves a new node unmeasured, for React Flow to measure", () => {
    const merged = mergeFlowNodes([], toFlow(pipeline()).nodes, null);
    expect(merged.every((n) => n.measured === undefined && !n.selected)).toBe(true);
  });
});
