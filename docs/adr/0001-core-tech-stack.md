# ADR 0001: Core technology stack

- **Status:** accepted
- **Date:** 2026-09-02
- **Deciders:** core team

## Context

Greenfield, self-hosted, multi-tenant agentic-chat platform. The agent loop must run on
the **Anthropic Agent SDK**, which ships for Python and TypeScript only. We want one
backend language, mainstream tooling, and a self-host story that fits Docker Compose.

## Decision

- **Backend:** Python 3.11+ (3.12 in CI/Docker), FastAPI + Uvicorn, async SQLAlchemy 2.0 +
  Alembic, Pydantic v2 / pydantic-settings.
- **Database:** PostgreSQL 16 + `pgvector` (also the v1 vector store, behind a
  `VectorStore` interface).
- **Queue/cache:** Redis 7 + Arq.
- **Object storage:** MinIO (S3 API).
- **Frontend:** Next.js 15 (App Router) + React 19 + TypeScript + Tailwind + shadcn-style
  UI; React Flow (`@xyflow/react`) for the node-graph builder.
- **Auth:** custom JWT (access + rotating refresh) with argon2id hashing.
- **Embeddings/rerank:** Voyage AI, with a local BGE fallback for offline self-host.
- **Observability:** structlog + OpenTelemetry; Langfuse as an optional compose profile.

## Consequences

- Positive: single backend language that the Agent SDK supports natively; one datastore
  for app data + vectors in v1; everything self-hostable.
- Negative: the Agent SDK spawns the Claude Code CLI as a subprocess, so API/worker images
  must bundle Node + the CLI (see ADR 0003). pgvector will need a migration path past
  ~1M chunks.
- Revisit: vector store choice at scale; multi-provider model support (post-v1).
