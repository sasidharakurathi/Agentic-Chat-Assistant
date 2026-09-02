# ADR 0004: The node-graph is a configuration surface that compiles to AssistantConfig

- **Status:** accepted (design; implemented from Phase 1)
- **Date:** 2026-09-02
- **Deciders:** core team

## Context

v1 ships a visual node-graph builder alongside a guided wizard and form config panels.
A true dynamic dataflow executor conflicts with how the Agent SDK works (the model decides
tool order at runtime) and is a large lift.

## Decision

- `AssistantConfig` (a Pydantic model) is the **single executable contract**. The runtime
  only ever consumes a compiled config.
- The **graph** (typed nodes + edges, with positions) is an authoring representation.
  `graph/compile.py` is a pure, deterministic, I/O-free function `compile(graph) ->
  AssistantConfig`. `graph/validate.py` gates publish.
- Edges are **declarative wiring** ("this KB / DB / tool / MCP server is available to this
  agent/subagent"), not execution order.
- Three editing surfaces write to one draft: canvas → `draft_graph` (→ compile →
  `draft_config`); form panels → `draft_config` (→ project a graph patch, positions
  preserved); wizard / AI recommend → `draft_graph`.
- Publishing snapshots **both** graph and compiled config onto an immutable
  `assistant_versions` row.

## Consequences

- Positive: all runtime work (Phases 2–5) targets the config and is unaffected by the
  canvas; the canvas can ship with fewer node types without blocking anything.
- Negative: we maintain a compiler plus a reverse `config → graph` projection; round-trip
  fidelity needs property tests (Phase 1 spike).
- Revisit: node granularity (one KB node vs per-source), whether `router` is a real node,
  and reverse-projection fidelity — all flagged as Phase 1 open items.
