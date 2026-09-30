import { describe, expect, it } from "vitest";

import type { Graph, GraphNode, ValidationResult } from "@/lib/api";

import {
  addEdge,
  agentTargetHandle,
  edgeKey,
  indexIssues,
  mainLineStops,
  makeRoom,
  mergeFlowNodes,
  nextNodeId,
  placeNear,
  removeNodes,
  STRUCTURAL_NODE_TYPES,
  tidyLayout,
  toFlow,
  wiredInto,
} from "./graph-sync";
import { modelName } from "@/lib/subagent-models";

import { problemSummary, publishBlockedReason, stationDetail, stationName } from "./station";

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
      errors: [
        { code: "illegal_edge", message: "no", node_id: null, edge: ["kb", "ag"], fix: null },
      ],
      warnings: [],
    };
    const { edges } = toFlow(pipeline(), indexIssues(validation));
    const bad = edges.find((e) => e.source === "kb")!;
    expect(bad.label).toBe("no");
    expect(bad.data?.problem).toBe("no");
    expect(edges.find((e) => e.source === "calc")!.label).toBeUndefined();
  });

  it("joins the Agent on the left for the main line, from above or below for capabilities", () => {
    const g = pipeline();
    // Agent at y=1: kb moved above it; calc and mem are below.
    g.nodes = g.nodes.map((n) => (n.id === "kb" ? { ...n, position: { x: -40, y: -100 } } : n));
    const { edges } = toFlow(g);
    const handle = Object.fromEntries(
      edges.filter((e) => e.target === "ag").map((e) => [e.source, e.targetHandle]),
    );
    expect(handle).toEqual({ guard: "left", kb: "top", calc: "bottom", mem: "bottom" });
    // Only edges into the Agent name a handle.
    expect(edges.find((e) => e.target === "out")!.targetHandle).toBeUndefined();
  });

  it("tells main-line edges from capability lines, and marks what can change data", () => {
    const g = pipeline();
    g.nodes.push({ ...node("db", "database"), data: { expose_write: true } });
    g.edges.push({ source: "db", target: "ag" });
    const { edges } = toFlow(g);
    const by = (s: string) => edges.find((e) => e.source === s)!.data!;
    expect(by("in").line).toBe("main");
    expect(by("guard").line).toBe("main");
    expect(by("kb")).toMatchObject({ line: "capability", family: "knowledge", writes: false });
    expect(by("calc")).toMatchObject({ line: "capability", family: "action" });
    expect(by("db").writes).toBe(true);
    expect(edges.every((e) => e.type === "route")).toBe(true);
  });

  it("lights a run's route and leaves the rest unlit", () => {
    const { nodes, edges } = toFlow(pipeline(), undefined, {}, new Set(["in", "guard", "ag"]), {
      times: new Map([["guard", 12]]),
      drawKey: "run-1",
    });
    expect(nodes.find((n) => n.id === "guard")!.data).toMatchObject({ trace: "lit", time: 12 });
    expect(nodes.find((n) => n.id === "kb")!.data.trace).toBe("dim");
    expect(edges.find((e) => e.source === "in")!.data).toMatchObject({
      trace: "lit",
      drawKey: "run-1",
    });
    expect(edges.find((e) => e.source === "kb")!.data!.trace).toBe("dim");
    expect(toFlow(pipeline()).edges[0].data!.trace).toBeUndefined();
  });

  it("numbers the main-line stations it reaches", () => {
    const { nodes } = toFlow(pipeline());
    const stop = Object.fromEntries(nodes.map((n) => [n.id, n.data.stop]));
    expect(stop).toMatchObject({ in: 1, guard: 2, ag: 3, out: 4 });
    expect(stop.kb).toBeUndefined();
  });
});

describe("mainLineStops", () => {
  it("closes up the numbers when a stop is missing, and counts a router when present", () => {
    const g = pipeline();
    g.nodes.push(node("r", "router"));
    g.edges = g.edges.filter((e) => !(e.source === "guard" && e.target === "ag"));
    g.edges.push({ source: "guard", target: "r" }, { source: "r", target: "ag" });
    expect(Object.fromEntries(mainLineStops(g))).toEqual({ in: 1, guard: 2, r: 3, ag: 4, out: 5 });
  });

  it("gives no number to a station the line doesn't reach", () => {
    const g = pipeline();
    g.edges = g.edges.filter((e) => e.target !== "ag" || e.source !== "guard");
    expect(Object.fromEntries(mainLineStops(g))).toEqual({ in: 1, guard: 2 });
  });
});

describe("agentTargetHandle", () => {
  it("is left for main-line sources whatever their height", () => {
    expect(agentTargetHandle(node("g", "guardrail", 0, -300), node("a", "agent"))).toBe("left");
    expect(agentTargetHandle(node("t", "tool", 0, -96), node("a", "agent"))).toBe("top");
    expect(agentTargetHandle(node("t", "tool", 0, 0), node("a", "agent"))).toBe("bottom");
  });
});

describe("placeNear", () => {
  it("lands below the anchor's row, on the grid", () => {
    const p = placeNear(pipeline(), "agent", 2);
    expect(p.y).toBeGreaterThan(1);
    expect(Math.abs(p.x % 24)).toBe(0);
    expect(Math.abs(p.y % 24)).toBe(0);
  });
});

describe("station words", () => {
  it("says what a station is in plain words", () => {
    expect(modelName("claude-haiku-4-5")).toBe("Haiku 4.5");
    expect(modelName("claude-sonnet-4-5-20250929")).toBe("Sonnet 4.5");
    expect(stationDetail({ type: "router", data: {} })).toBe("Picks the effort for each message");
    expect(stationDetail({ type: "knowledge_base", data: {} })).toBe("Results per search not set");
    expect(
      stationDetail({ type: "knowledge_base", data: { retrieval: { rerank_top_n: 8 } } }),
    ).toBe("8 results per search");
    expect(stationDetail({ type: "database", data: {} })).toBe("Connection removed. Pick another.");
    expect(stationDetail({ type: "database", data: {} }, "Shop PG")).toBe("Database");
    expect(stationName({ type: "database", data: {} }, "Shop PG")).toBe("Shop PG");
    expect(stationName({ type: "data_source", data: {} }, "refund-policy.md")).toBe(
      "refund-policy.md",
    );
    expect(stationName({ type: "data_source", data: {} })).toBe("Data source");
    expect(stationName({ type: "tool", data: { key: "calculator" } }, "ignored")).not.toBe(
      "ignored",
    );
    expect(
      stationDetail({ type: "mcp_server", data: { tool_allowlist: ["a", "b"] } }, "Files"),
    ).toBe("2 tools allowed");
    expect(stationName({ type: "tool", data: { key: "web_search" } })).toBe("Web search");
  });

  it("counts problems with correct plurals", () => {
    expect(problemSummary(2, 1)).toBe("2 problems, 1 to check");
    expect(problemSummary(1, 0)).toBe("1 problem");
    expect(problemSummary(0, 1)).toBe("1 thing to check");
    expect(problemSummary(0, 3)).toBe("3 things to check");
    expect(publishBlockedReason(2)).toBe("Fix 2 problems to publish");
    expect(publishBlockedReason(1)).toBe("Fix 1 problem to publish");
  });
});

describe("indexIssues", () => {
  it("files errors and warnings under their node, and edge errors under the edge", () => {
    const issues = indexIssues({
      errors: [
        { code: "a", message: "kb broken", node_id: "kb", edge: null, fix: null },
        { code: "b", message: "kb twice", node_id: "kb", edge: null, fix: null },
        { code: "c", message: "bad wire", node_id: null, edge: ["calc", "ag"], fix: null },
      ],
      warnings: [
        { code: "d", message: "defaults in use", node_id: "guard", edge: null, fix: null },
        { code: "e", message: "graph-wide", node_id: null, edge: null, fix: null },
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

  it("puts the main line on one row, and nothing else on it", () => {
    const g = pipeline();
    g.nodes.push(node("router", "router"));
    const y = Object.fromEntries(tidyLayout(g).nodes.map((n) => [n.id, n.position.y]));
    for (const id of ["in", "guard", "router", "ag", "out"]) expect(y[id]).toBe(0);
    for (const id of ["kb", "calc", "mem"]) expect(y[id]).not.toBe(0);
  });

  it("stacks a column without overlaps, never on the main row, balanced within one row", () => {
    const g = pipeline();
    g.nodes.push(node("db", "database"), node("web", "tool"));
    const tidy = tidyLayout(g);
    const column = tidy.nodes.filter(
      (n) => n.position.x === tidy.nodes.find((m) => m.id === "kb")!.position.x,
    );
    const ys = column.map((n) => n.position.y);
    expect(ys).toHaveLength(5);
    expect(new Set(ys).size).toBe(ys.length);
    expect(ys).not.toContain(0);
    const above = ys.filter((v) => v < 0).length;
    const below = ys.filter((v) => v > 0).length;
    expect(Math.abs(above - below)).toBeLessThanOrEqual(1);
    // Rows land on the 24px snap grid.
    for (const n of tidy.nodes) expect(Math.abs(n.position.y % 24)).toBe(0);
    expect(tidy.nodes.find((n) => n.id === "ag")!.position.y).toBe(0);
  });

  it("keeps data sources on their knowledge base's side of the main line", () => {
    const g = pipeline();
    g.nodes.push(
      node("ds1", "data_source"),
      node("ds2", "data_source"),
      node("ds3", "data_source"),
    );
    const y = Object.fromEntries(tidyLayout(g).nodes.map((n) => [n.id, n.position.y]));
    for (const id of ["ds1", "ds2", "ds3"]) expect(Math.sign(y[id])).toBe(Math.sign(y.kb));
    expect(new Set([y.ds1, y.ds2, y.ds3]).size).toBe(3);
  });

  it("starts subagents outside the capability rows", () => {
    const g = pipeline();
    g.nodes.push(node("sub:sql", "subagent"), node("sub:research", "subagent"));
    const tidy = tidyLayout(g);
    const y = Object.fromEntries(tidy.nodes.map((n) => [n.id, n.position.y]));
    const deepest = Math.max(...["kb", "calc", "mem"].map((id) => Math.abs(y[id])));
    for (const id of ["sub:sql", "sub:research"]) {
      expect(Math.abs(y[id])).toBeGreaterThan(deepest);
    }
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

describe("wiredInto", () => {
  it("names the capabilities wired into a node (a subagent's own)", () => {
    const graph: Graph = {
      schema_version: 1,
      nodes: [
        { id: "sub:sql", type: "subagent", position: { x: 0, y: 0 }, data: { role: "sql" } },
        { id: "db:1", type: "database", position: { x: 0, y: 0 }, data: { connection_id: "c1" } },
        {
          id: "tool:calculator",
          type: "tool",
          position: { x: 0, y: 0 },
          data: { key: "calculator" },
        },
        { id: "agent", type: "agent", position: { x: 0, y: 0 }, data: {} },
      ],
      edges: [
        { source: "db:1", target: "sub:sql" },
        { source: "tool:calculator", target: "sub:sql" },
        { source: "sub:sql", target: "agent" },
      ],
    };
    expect(wiredInto(graph, "sub:sql", { c1: "Shop PG" })).toEqual([
      "Database: Shop PG",
      "Tool: calculator",
    ]);
    expect(wiredInto(graph, "agent")).toEqual([]);
    expect(wiredInto(graph, null)).toEqual([]);
  });
});

describe("accessible names on the canvas", () => {
  it("names stations and connections in words, never by raw id", () => {
    const g = pipeline();
    g.nodes.push({
      id: "ds-2",
      type: "data_source",
      position: { x: 0, y: 0 },
      data: { data_source_id: "s1" },
    });
    g.edges.push({ source: "ds-2", target: "kb" });
    const validation: ValidationResult = {
      errors: [{ code: "x", message: "Pick a model.", node_id: "ag", edge: null, fix: null }],
      warnings: [],
    };
    const { nodes, edges } = toFlow(g, indexIssues(validation), { s1: "Refund policy" });
    const label = (id: string) => nodes.find((n) => n.id === id)!.ariaLabel;
    expect(label("in")).toBe("Input, step 1 of the pipeline");
    expect(label("ds-2")).toBe("Refund policy, Data source");
    expect(label("ag")).toBe("Agent, step 3 of the pipeline, 1 problem: Pick a model.");
    const edge = (s: string, t: string) =>
      edges.find((e) => e.source === s && e.target === t)!.ariaLabel;
    expect(edge("ds-2", "kb")).toBe("Connection from Refund policy to Knowledge base");
    expect(edge("in", "guard")).toBe("Connection from Input to Guardrails");
  });
});

describe("makeRoom", () => {
  it("moves capabilities off the spot a new main-line station takes, into free rows", () => {
    // The old layout: a knowledge base on the agent's row, a tool above it.
    const g: Graph = {
      schema_version: 1,
      nodes: [
        node("guard", "guardrail", 260, 0),
        node("kb", "knowledge_base", 640, 0),
        node("calc", "tool", 640, -96),
        node("ag", "agent", 1040, 0),
      ],
      edges: [],
    };
    const next = makeRoom(g, { x: 648, y: 0 });
    const at = (id: string) => next.nodes.find((n) => n.id === id)!.position;
    expect(at("kb")).toEqual({ x: 640, y: 96 });
    expect(at("calc")).toEqual({ x: 640, y: -96 });
    expect(at("guard")).toEqual({ x: 260, y: 0 });
    expect(at("ag")).toEqual({ x: 1040, y: 0 });
  });

  it("leaves a graph with room alone", () => {
    const g = tidyLayout(pipeline());
    expect(makeRoom(g, { x: 820, y: 0 })).toBe(g);
  });
});
