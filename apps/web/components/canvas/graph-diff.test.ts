import { describe, expect, it } from "vitest";

import type { Graph, GraphNode, VersionDiff } from "@/lib/api";

import { changesFor, compareGraphs, comparisonSummary } from "./graph-diff";
import {
  connectionsOf,
  edgeKey,
  mergeFlowEdges,
  mergeFlowNodes,
  readingOrder,
  tidyLayout,
  toFlow,
} from "./graph-sync";

const node = (id: string, type: string, x = 0, y = 0, data = {}): GraphNode => ({
  id,
  type,
  position: { x, y },
  data,
});

const MAIN = [
  { source: "in", target: "guard" },
  { source: "guard", target: "ag" },
  { source: "ag", target: "out" },
];

/** Version 1: a knowledge base and a calculator. */
const v1: Graph = {
  schema_version: 1,
  nodes: [
    node("in", "input", 0, 0),
    node("guard", "guardrail", 260, 0, { rules: [] }),
    node("ag", "agent", 1040, 0, { system_prompt: "Be brief." }),
    node("out", "output", 1320, 0),
    node("kb", "knowledge_base", 640, -96),
    node("tool:calculator", "tool", 640, 96, { key: "calculator" }),
  ],
  edges: [...MAIN, { source: "kb", target: "ag" }, { source: "tool:calculator", target: "ag" }],
};

/** Version 2: the calculator is gone, a database arrived, the prompt and
 *  the rules changed. */
const v2: Graph = {
  schema_version: 1,
  nodes: [
    node("in", "input", 0, 0),
    node("guard", "guardrail", 260, 0, { rules: ["Stay on topic."] }),
    node("ag", "agent", 1040, 0, { system_prompt: "Be thorough." }),
    node("out", "output", 1320, 0),
    node("kb", "knowledge_base", 640, -96),
    node("db.orders", "database", 640, 96, { connection_id: "c1" }),
  ],
  edges: [...MAIN, { source: "kb", target: "ag" }, { source: "db.orders", target: "ag" }],
};

const diff: VersionDiff["graph_diff"] = {
  nodes_added: ["db.orders"],
  nodes_removed: ["tool:calculator"],
  nodes_changed: [
    { path: "ag.system_prompt", op: "changed", before: "Be brief.", after: "Be thorough." },
    { path: "ag.models.main.effort", op: "changed", before: "low", after: "high" },
    { path: "guard.rules", op: "changed", before: [], after: ["Stay on topic."] },
  ],
  edges_added: [["db.orders", "ag"]],
  edges_removed: [["tool:calculator", "ag"]],
  node_types: {},
};

describe("compareGraphs", () => {
  const c = compareGraphs(v1, v2, diff);

  it("draws the newer version with what was removed put back", () => {
    expect(c.graph.nodes.map((n) => n.id).sort()).toEqual(
      ["ag", "db.orders", "guard", "in", "kb", "out", "tool:calculator"].sort(),
    );
    // The removed line too, or the removed station would float unconnected.
    expect(c.graph.edges.some((e) => e.source === "tool:calculator" && e.target === "ag")).toBe(
      true,
    );
    expect(c.graph.edges).toHaveLength(v2.edges.length + 1);
  });

  it("marks each station once: added and removed win over changed", () => {
    expect(Object.fromEntries(c.nodes)).toEqual({
      ag: "changed",
      guard: "changed",
      "db.orders": "added",
      "tool:calculator": "removed",
    });
    expect(Object.fromEntries(c.counts)).toEqual({ ag: 2, guard: 1 });
  });

  it("marks the lines by the pair they join", () => {
    expect(Object.fromEntries(c.edges)).toEqual({
      [edgeKey("db.orders", "ag")]: "added",
      [edgeKey("tool:calculator", "ag")]: "removed",
    });
  });

  it("lays the drawing out the same way every time, with no two stations on one spot", () => {
    expect(c.graph).toEqual(tidyLayout(c.graph));
    expect(compareGraphs(v1, v2, diff).graph).toEqual(c.graph);
    const spots = c.graph.nodes.map((n) => `${n.position.x},${n.position.y}`);
    expect(new Set(spots).size).toBe(spots.length);
  });

  it("does not touch the graphs it was given", () => {
    const before = JSON.stringify([v1, v2]);
    compareGraphs(v1, v2, diff);
    expect(JSON.stringify([v1, v2])).toBe(before);
  });

  it("gives a setting to the station whose id it starts with, dots and all", () => {
    const dotted = compareGraphs(v1, v2, {
      ...diff,
      nodes_added: [],
      nodes_removed: [],
      edges_added: [],
      edges_removed: [],
      nodes_changed: [
        { path: "db.orders.expose_write", op: "changed", before: false, after: true },
      ],
    });
    // "db.orders" is a station; "db" is not. The longest id that fits wins.
    expect(Object.fromEntries(dotted.nodes)).toEqual({ "db.orders": "changed" });
    expect(changesFor(diff.nodes_changed, "ag", c.graph)).toHaveLength(2);
    expect(changesFor(diff.nodes_changed, "kb", c.graph)).toEqual([]);
  });

  it("says nothing when the pipelines are the same", () => {
    const same = compareGraphs(v1, v1, {
      nodes_added: [],
      nodes_removed: [],
      nodes_changed: [],
      edges_added: [],
      edges_removed: [],
      node_types: {},
    });
    expect(comparisonSummary(same)).toBeNull();
    expect(comparisonSummary(c)).toBe("2 added, 2 removed, 2 changed");
  });
});

describe("a comparison on the canvas", () => {
  const c = compareGraphs(v1, v2, diff);
  const flow = toFlow(c.graph, undefined, { c1: "Orders DB" }, null, {
    diff: { nodes: c.nodes, edges: c.edges, counts: c.counts },
  });
  const station = (id: string) => flow.nodes.find((n) => n.id === id)!;
  const line = (source: string) =>
    flow.edges.find((e) => e.source === source && e.target === "ag")!;

  it("lights what changed and leaves the rest unlit", () => {
    expect(station("db.orders").data).toMatchObject({ diff: "added", trace: "lit" });
    expect(station("tool:calculator").data).toMatchObject({ diff: "removed", trace: "lit" });
    expect(station("ag").data).toMatchObject({ diff: "changed", diffCount: 2, trace: "lit" });
    expect(station("kb").data.diff).toBeUndefined();
    expect(station("kb").data.trace).toBe("dim");
    expect(line("db.orders").data).toMatchObject({ diff: "added", trace: "lit" });
    expect(line("tool:calculator").data).toMatchObject({ diff: "removed", trace: "lit" });
    expect(line("kb").data).toMatchObject({ trace: "dim" });
    expect(line("kb").data?.diff).toBeUndefined();
  });

  it("says the change in each station's and line's name, and nothing about a run", () => {
    expect(station("db.orders").ariaLabel).toBe("Orders DB, Database, added in the newer version");
    expect(station("tool:calculator").ariaLabel).toContain("removed in the newer version");
    expect(station("ag").ariaLabel).toContain("changed between the versions");
    // Unlit here means unchanged, not "not used in this run".
    expect(station("kb").ariaLabel).toBe("Knowledge base");
    expect(line("tool:calculator").ariaLabel).toBe(
      "Connection from Calculator to Agent, removed in the newer version",
    );
  });

  it("marks nothing when no comparison is being shown", () => {
    const plain = toFlow(v2);
    expect(plain.nodes.every((n) => n.data.diff === undefined && n.data.trace === undefined)).toBe(
      true,
    );
    expect(plain.edges.every((e) => e.data?.diff === undefined)).toBe(true);
  });
});

describe("readingOrder", () => {
  it("is the main line stop by stop, then the rest by column and row", () => {
    const g: Graph = {
      ...v2,
      // Stored in the order they happened to be added.
      nodes: [v2.nodes[5], v2.nodes[3], v2.nodes[4], v2.nodes[2], v2.nodes[0], v2.nodes[1]],
    };
    expect(readingOrder(g)).toEqual(["in", "guard", "ag", "out", "kb", "db.orders"]);
    // The canvas hands React Flow its stations in that order, which is the
    // order Tab and a screen reader meet them in.
    expect(toFlow(g).nodes.map((n) => n.id)).toEqual(readingOrder(g));
  });

  it("puts a main-line station the line does not reach after the ones it does", () => {
    const g: Graph = { ...v2, edges: v2.edges.filter((e) => e.target !== "out") };
    const order = readingOrder(g);
    expect(order.slice(0, 3)).toEqual(["in", "guard", "ag"]);
    expect(order).toContain("out");
  });
});

describe("connectionsOf", () => {
  const RULES: [string, string][] = [
    ["input", "guardrail"],
    ["guardrail", "agent"],
    ["agent", "output"],
    ["knowledge_base", "agent"],
    ["database", "agent"],
    ["database", "subagent"],
    ["subagent", "agent"],
  ];
  const g: Graph = {
    ...v2,
    nodes: [...v2.nodes, node("sub:sql", "subagent", 820, 192, { role: "sql" })],
  };

  it("lists what a station goes to, comes from, and may be connected to", () => {
    const db = connectionsOf(g, "db.orders", RULES, { c1: "Orders DB" })!;
    expect(db.outgoing).toEqual([{ id: "ag", label: "Agent" }]);
    expect(db.incoming).toEqual([]);
    // Already wired to the Agent, so only the subagent is offered.
    expect(db.targets.map((t) => t.id)).toEqual(["sub:sql"]);

    const agent = connectionsOf(g, "ag", RULES, { c1: "Orders DB" })!;
    expect(agent.incoming.map((i) => i.label)).toEqual([
      "Guardrails",
      "Knowledge base",
      "Orders DB",
    ]);
    expect(agent.outgoing.map((o) => o.label)).toEqual(["Output"]);
    expect(agent.targets).toEqual([]);
  });

  it("offers only what the wiring rules allow", () => {
    const sub = connectionsOf(g, "sub:sql", RULES)!;
    expect(sub.targets.map((t) => t.id)).toEqual(["ag"]);
    expect(connectionsOf(g, "in", RULES)!.targets).toEqual([]); // already wired to its only target
    // Rules not loaded yet: nothing is offered, rather than everything.
    expect(connectionsOf(g, "sub:sql", null)!.targets).toEqual([]);
    expect(connectionsOf(g, "nope", RULES)).toBeNull();
  });
});

describe("a large graph", () => {
  /** 150 stations: the pipeline, a knowledge base, and 145 data sources. */
  function large(): Graph {
    const sources = Array.from({ length: 145 }, (_, i) =>
      node(`src-${i}`, "data_source", 0, 0, { data_source_id: `d${i}` }),
    );
    return tidyLayout({
      schema_version: 1,
      nodes: [...v1.nodes.slice(0, 5), ...sources],
      edges: [
        ...MAIN,
        { source: "kb", target: "ag" },
        ...sources.map((s) => ({ source: s.id, target: "kb" })),
      ],
    });
  }

  it("re-renders nothing when nothing changed", () => {
    const g = large();
    const held = mergeFlowNodes([], toFlow(g).nodes, null).map((n) => ({
      ...n,
      measured: { width: 180, height: 52 },
    }));
    // A save comes back: the same graph, as brand-new objects.
    const again = mergeFlowNodes(held, toFlow(JSON.parse(JSON.stringify(g)) as Graph).nodes, null);
    expect(again).toHaveLength(150);
    expect(again.every((n, i) => n === held[i])).toBe(true);

    const lines = mergeFlowEdges([], toFlow(g).edges);
    const linesAgain = mergeFlowEdges(lines, toFlow(JSON.parse(JSON.stringify(g)) as Graph).edges);
    expect(linesAgain.every((e, i) => e === lines[i])).toBe(true);
  });

  it("re-renders only the station that changed, or was picked", () => {
    const g = large();
    const held = mergeFlowNodes([], toFlow(g).nodes, null).map((n) => ({
      ...n,
      measured: { width: 180, height: 52 },
    }));
    const moved: Graph = {
      ...g,
      nodes: g.nodes.map((n) => (n.id === "src-7" ? { ...n, position: { x: 12, y: 3456 } } : n)),
    };
    const kept = new Set<object>(held);
    const after = mergeFlowNodes(held, toFlow(moved).nodes, null);
    const fresh = after.filter((n) => !kept.has(n)).map((n) => n.id);
    expect(fresh).toEqual(["src-7"]);
    // And it keeps the size React Flow measured, so it is never hidden.
    expect(after.find((n) => n.id === "src-7")!.measured).toEqual({ width: 180, height: 52 });

    const picked = mergeFlowNodes(held, toFlow(g).nodes, "src-3");
    expect(picked.filter((n) => !kept.has(n)).map((n) => n.id)).toEqual(["src-3"]);
    expect(picked.find((n) => n.id === "src-3")!.selected).toBe(true);
  });

  it("keeps a line selected across an update, and replaces one that changed", () => {
    const g = large();
    const lines = mergeFlowEdges([], toFlow(g).edges).map((e, i) =>
      i === 4 ? { ...e, selected: true } : e,
    );
    const broken = toFlow(g, {
      nodes: new Map(),
      edges: new Map([[edgeKey(lines[9].source, lines[9].target), ["no"]]]),
    }).edges;
    const after = mergeFlowEdges(lines, broken);
    expect(after[4]).toBe(lines[4]);
    expect(after[4].selected).toBe(true);
    expect(after[9]).not.toBe(lines[9]);
    expect(after[9].data?.problem).toBe("no");
  });

  it("is laid out and drawn in a few milliseconds", () => {
    const g = large();
    const started = performance.now();
    for (let i = 0; i < 20; i++) {
      const flow = toFlow(tidyLayout(g));
      mergeFlowNodes(flow.nodes, flow.nodes, null);
    }
    const each = (performance.now() - started) / 20;
    // Measured at about 2 ms; the bound is loose so a slow CI machine passes
    // and an accidental quadratic does not.
    expect(each).toBeLessThan(60);
  });
});

describe("whose setting a change is", () => {
  const none = {
    nodes_added: [],
    nodes_removed: [],
    edges_added: [],
    edges_removed: [],
    node_types: {},
  };
  // "db" and "db.orders" are both stations: one id is the start of the other.
  const both: Graph = { ...v2, nodes: [...v2.nodes, node("db", "database", 640, 288)] };

  it("goes to the longest station id that fits, not the first", () => {
    const c = compareGraphs(both, both, {
      ...none,
      nodes_changed: [
        { path: "db.orders.expose_write", op: "changed", before: false, after: true },
        { path: "db.expose_write", op: "changed", before: false, after: true },
      ],
    });
    expect(Object.fromEntries(c.counts)).toEqual({ "db.orders": 1, db: 1 });
    expect(changesFor([{ path: "db.orders.expose_write" }], "db", c.graph)).toEqual([]);
  });

  it("is nobody's when the id only starts the same", () => {
    const c = compareGraphs(v2, v2, {
      ...none,
      nodes_changed: [{ path: "agx.system_prompt", op: "changed", before: "a", after: "b" }],
    });
    expect(c.nodes.size).toBe(0);
  });

  it("does not call a new station changed", () => {
    const c = compareGraphs(v1, v2, {
      ...diff,
      nodes_changed: [
        ...diff.nodes_changed,
        { path: "db.orders.connection_id", op: "added", before: null, after: "c1" },
      ],
    });
    expect(c.nodes.get("db.orders")).toBe("added");
    expect(c.counts.has("db.orders")).toBe(false);
  });
});
