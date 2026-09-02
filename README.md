# Assistant Studio

Self-hosted, multi-tenant platform for building **dynamic agentic chat assistants** —
each assistant is a configurable pipeline of RAG, database integrations, tools, and MCP
servers, authored through a visual node-graph builder, a guided wizard, or form panels,
and run on the **Anthropic Agent SDK (Python)**.

- Product spec: [`docs/PRD.md`](docs/PRD.md)
- Implementation plan: [`docs/IMPLEMENTATION_PLAN.md`](docs/IMPLEMENTATION_PLAN.md)
- Architecture decisions: [`docs/adr/`](docs/adr/)

> Status: **Phase 0 — foundations & scaffolding.**

## Repository layout

```
apps/api        FastAPI backend (Python 3.11+)
apps/web        Next.js 15 frontend
packages/shared Shared TypeScript types (OpenAPI-generated later)
docker/         Dockerfiles + entrypoints
docs/           PRD, plan, ADRs
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
```

`.\scripts\check.ps1` runs everything CI runs (ruff, mypy, pytest, web typecheck + lint).

### macOS / Linux (bash)

```bash
docker compose up -d postgres redis minio

python -m venv .venv
.venv/bin/python -m pip install -U pip
.venv/bin/python -m pip install -e "apps/api[dev]"
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

| PowerShell script       | `just` / `make`  | what                                   |
| ----------------------- | ---------------- | -------------------------------------- |
| `.\scripts\setup.ps1`   | `setup`          | venv + deps (api + web) + `.env`       |
| —                       | `up` / `down`    | datastores (+ `up-all` for containers) |
| `.\scripts\migrate.ps1` | `migrate`        | apply DB migrations                    |
| `.\scripts\seed.ps1`    | `seed`           | create a demo org + admin user         |
| `.\scripts\check.ps1`   | `check`          | lint + typecheck + test (what CI runs) |
| `.\scripts\dev-api.ps1` | `api`            | run the API with autoreload            |
| `.\scripts\dev-web.ps1` | `web`            | run the Next.js dev server             |

## Contributing

See [`CONTRIBUTING.md`](CONTRIBUTING.md). Version control is managed by the core team;
this repo ships no git automation.
