# Assistant Studio

Self-hosted, multi-tenant platform for building **dynamic agentic chat assistants** —
each assistant is a configurable pipeline of RAG, database integrations, tools, and MCP
servers, authored through a visual node-graph builder, a guided wizard, or form panels,
and run on the **Anthropic Agent SDK (Python)**.

- Product spec: [`docs/PRD.md`](docs/PRD.md)
- Implementation plan: [`docs/IMPLEMENTATION_PLAN.md`](docs/IMPLEMENTATION_PLAN.md)
- **Build log / explainer: [`docs/EXPLAINER.md`](docs/EXPLAINER.md)** — task-by-task walkthrough
  of what exists and why. **Start here if you're picking this up.**
- Architecture decisions: [`docs/adr/`](docs/adr/)
- Database access, safely: [`docs/DATABASE_ACCESS.md`](docs/DATABASE_ACCESS.md)
- **Trying all of it, end to end: [`docs/TEST_DRIVE.md`](docs/TEST_DRIVE.md)** (the production stack, the real model)
- Manual testing, with test data: [`docs/MANUAL_TESTING.md`](docs/MANUAL_TESTING.md) (`.\scripts\seed-testdata.ps1` loads the data)
- **Using it: [`docs/USER_GUIDE.md`](docs/USER_GUIDE.md)**. **Running it: [`docs/OPERATIONS.md`](docs/OPERATIONS.md)**
- What it defends against, and what is still open: [`docs/THREAT_MODEL.md`](docs/THREAT_MODEL.md)
- How the interface is designed: [`docs/DESIGN.md`](docs/DESIGN.md)

## Where things stand

> **All six phases of the plan are built, and 1.0.0 is prepared** (not
> yet tagged): see [`CHANGELOG.md`](CHANGELOG.md) and
> [`docs/RELEASE_NOTES_v1.0.0.md`](docs/RELEASE_NOTES_v1.0.0.md).
> `docs/EXPLAINER.md` walks through every task.
> `.\scripts\check.ps1` runs everything CI runs.

**Working end to end today:**

- Auth, orgs, multi-tenant isolation, RBAC with rank rules, audit log, invites,
  and sign-up by invitation for a server on the internet
- Assistants: a visual node-graph canvas (drawn as a line diagram), form
  panels, the compiled config and a guided setup over one `AssistantConfig`;
  AI help writing a prompt or a whole pipeline; one-click fixes for wiring
  problems; immutable published versions with a history and diff view; three
  sample assistants to start from
- Streaming chat over SSE with Stop, run details and a trace you can show on
  the canvas, budgets per conversation, assistant and org, rate limits, a
  usage ledger
- **RAG**: files / URLs / pasted text → Arq ingestion → hybrid retrieval
  (pgvector + Postgres full-text, RRF, reranked) → cited answers with a sources
  panel
- **Databases**: Postgres, MySQL, SQLite and MongoDB behind a parse-based SQL
  guard, per-connection permission profiles, envelope-encrypted credentials,
  and human approval (showing the exact statement) before any write. See
  [`docs/DATABASE_ACCESS.md`](docs/DATABASE_ACCESS.md) for how to give an
  assistant database access safely.
- **Tools and MCP**: web search, HTTP requests behind an SSRF guard, a
  calculator and a clock; remote and local-command MCP servers, the latter in
  a sandboxed runner, with per-tool approval
- **Agent depth**: subagents, a router, conversation memory with summaries, a
  memory tool, guardrails against prompt injection and data leaving
- **Evals**: suites of test questions scored by free checks, retrieval labels
  and a judge model; a regression gate in CI
- **Operations**: a production compose file with TLS, a preflight that refuses
  a bad configuration, `/metrics` with a dashboard and alerts, tested
  backup and restore scripts, an upgrade runbook

**Costs nothing to run.** `.env` pins `AGENT_DRIVER=fake` (offline deterministic chat
driver) and `RAG_OFFLINE=1` (local `bge-m3` embeddings + local cross-encoder reranker).
Real Claude / Voyage calls only happen if you deliberately flip those.

## Repository layout

```
apps/api        FastAPI backend (Python 3.11+)
  app/agent/    agent runtime: drivers, capability tools, options, approvals
  app/graph/    node-graph model, validation, graph <-> config compiler
  app/rag/      parsers, chunker, embedders, rerankers, vectorstore, retrieval
apps/web        Next.js 15 frontend
packages/shared API types generated from the OpenAPI schema
scripts/        PowerShell dev entry points (setup, migrate, seed, dev-*, check)
docker/         Dockerfiles + entrypoints
deploy/         production: proxy config, env template, backup scripts, dashboards
docs/           PRD, plan, explainer, ADRs
```

## Prerequisites

| Tool                | Version                   | Notes                                |
| ------------------- | ------------------------- | ------------------------------------ |
| Python              | 3.11+ (3.12 in CI/Docker) | backend                              |
| Node.js             | 20+ (24 tested)           | frontend; uses **npm workspaces**    |
| Docker + Compose v2 | recent                    | Postgres + Redis + MinIO             |
| `just` _(optional)_ | latest                    | task runner; a `Makefile` mirrors it |

## Quick start

Run everything from the **repo root**. A datastore is needed for the API — either start
Docker Desktop and run the compose datastores, or point `DATABASE_URL` at SQLite in `.env`
for a zero-infra start (`DATABASE_URL=sqlite+aiosqlite:///./dev.db`).

### Windows (PowerShell)

PowerShell 5.1 has no `&&`; use the scripts in `.\scripts\`:

```powershell
.\scripts\setup.ps1                        # venv + API deps + npm install + .env
docker compose up -d postgres redis minio  # or edit .env to use SQLite
.\scripts\migrate.ps1
.\scripts\seed.ps1
.\scripts\dev-api.ps1                       # terminal 1  -> http://localhost:8000
.\scripts\dev-web.ps1                       # terminal 2  -> http://localhost:3000
.\scripts\dev-worker.ps1                    # terminal 3  -> Arq worker (data-source ingestion)
```

The worker is only needed for RAG — without it, a new data source just sits at
`pending` instead of getting indexed.

`.\scripts\check.ps1` runs everything CI runs: ruff, mypy, the unit suite
(`-m "not integration"`), then the integration suite (`-m integration`) as a separate,
clearly-labelled step. Integration tests need Docker Postgres + MinIO up, and on first
run will download ~2 GB of local embedding/reranker models. CI runs unit tests only.

### macOS / Linux (bash)

```bash
docker compose up -d postgres redis minio

python -m venv .venv
.venv/bin/python -m pip install -U pip
.venv/bin/python -m pip install -e "apps/api[dev,observability]"
cp .env.example .env

.venv/bin/python -m alembic -c apps/api/alembic.ini upgrade head
(cd apps/api && ../../.venv/bin/python -m scripts.seed)
.venv/bin/python -m uvicorn app.main:app --app-dir apps/api --reload

npm install && npm run dev -w web   # separate terminal
```

- API: <http://localhost:8000> — docs at `/docs`, health at `/healthz` / `/readyz`
- Web: <http://localhost:3000>

With `just` installed: `just setup`, `just up`, `just migrate`, `just seed`, `just check`
(Windows `just` needs PowerShell 7 / `pwsh`).

## Common tasks

| PowerShell script          | `just` / `make` | what                                   |
| -------------------------- | --------------- | -------------------------------------- |
| `.\scripts\setup.ps1`      | `setup`         | venv + deps (api + web) + `.env`       |
| —                          | `up` / `down`   | datastores (+ `up-all` for containers) |
| `.\scripts\migrate.ps1`    | `migrate`       | apply DB migrations                    |
| `.\scripts\seed.ps1`       | `seed`          | create a demo org + admin user         |
| `.\scripts\check.ps1`      | `check`         | lint + typecheck + test (what CI runs) |
| `.\scripts\dev-api.ps1`    | `api`           | run the API with autoreload            |
| `.\scripts\dev-web.ps1`    | `web`           | run the Next.js dev server             |
| `.\scripts\dev-worker.ps1` | —               | run the Arq worker (RAG ingestion)     |

## Contributing

See [`CONTRIBUTING.md`](CONTRIBUTING.md). Version control is managed by the core team;
this repo ships no git automation.
