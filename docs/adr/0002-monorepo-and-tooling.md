# ADR 0002: Monorepo layout and tooling

- **Status:** accepted
- **Date:** 2026-09-02
- **Deciders:** core team

## Context

Two developers plus Claude Code, building a backend, a frontend, and shared types. We want
low ceremony, fast onboarding on Windows and macOS, and no dependency on tools that aren't
trivially installable.

## Decision

- **Single repo**, `apps/api` + `apps/web` + `packages/shared`.
- **JS:** npm workspaces (Node 20+). No pnpm/yarn — npm ships with Node and needs no extra
  install. `packages/shared` will hold OpenAPI-generated TS types.
- **Python:** one PEP 621 `pyproject.toml` under `apps/api`, hatchling build backend.
  Works with `uv`, `pip`, or `poetry`; docs use `pip`. CI uses `uv` for speed.
- **Task runner:** `justfile` as primary, with a `Makefile` mirroring every recipe for
  environments without `just` (common on Windows).
- **Python version:** require `>=3.11` so the current dev machines work today; CI matrix
  and Docker images pin 3.12. Avoid 3.12-only syntax in the codebase.
- **Quality gates:** ruff (lint+format), mypy, pytest for Python; eslint, prettier, tsc
  for web. `just check` == CI.

## Consequences

- Positive: `git clone` → `just setup` with only Python + Node + Docker preinstalled.
- Negative: npm workspaces are less strict than pnpm about phantom deps; we accept that for
  now. Supporting both `just` and `make` is minor duplication.
- Revisit: switch to pnpm if the web dependency graph gets large or we need stricter
  isolation.
