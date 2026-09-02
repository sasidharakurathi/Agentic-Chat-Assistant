# Assistant Studio — task runner.  Install `just`: https://github.com/casey/just
# Run every recipe from the repo root.
#
# Windows: `just` recipes here need PowerShell 7 (`pwsh`) for `&&`. Windows
# PowerShell 5.1 users should use the scripts in .\scripts\ instead
# (setup.ps1, migrate.ps1, seed.ps1, dev-api.ps1, dev-web.ps1, check.ps1).

set windows-shell := ["pwsh", "-NoLogo", "-NoProfile", "-Command"]

python := if os_family() == "windows" { ".venv/Scripts/python.exe" } else { ".venv/bin/python" }
alembic := python + " -m alembic -c apps/api/alembic.ini"

# List recipes
default:
    @just --list

# ── Setup ────────────────────────────────────────────────────

setup-api:
    python -m venv .venv
    {{python}} -m pip install -U pip
    {{python}} -m pip install -e "apps/api[dev]"

setup-web:
    npm install

setup: setup-api setup-web
    @echo "Copy .env.example -> .env and fill in secrets."

# ── Infra (Docker) ───────────────────────────────────────────

# Datastores only (postgres, redis, minio)
up:
    docker compose up -d

# Datastores + build & run api + web containers
up-all:
    docker compose --profile apps up -d --build

down:
    docker compose --profile apps down

# Datastores + the optional Langfuse stack
up-observability:
    docker compose -f docker-compose.yml -f docker-compose.observability.yml up -d

logs service="":
    docker compose logs -f {{service}}

# ── API ──────────────────────────────────────────────────────

api:
    {{python}} -m uvicorn app.main:app --app-dir apps/api --reload --host 0.0.0.0 --port 8000

migrate:
    {{alembic}} upgrade head

# just migration name="add widgets table"
migration name:
    {{alembic}} revision --autogenerate -m "{{name}}"

seed:
    cd apps/api && ../../{{python}} -m scripts.seed

# ── Web ──────────────────────────────────────────────────────

web:
    npm run dev -w web

# ── Quality ──────────────────────────────────────────────────

test:
    {{python}} -m pytest apps/api -q

lint:
    {{python}} -m ruff check apps/api
    {{python}} -m ruff format --check apps/api
    npm run lint -w web

fmt:
    {{python}} -m ruff format apps/api
    {{python}} -m ruff check --fix apps/api
    npm run format

typecheck:
    cd apps/api && ../../{{python}} -m mypy app scripts
    npm run typecheck -w web

check: lint typecheck test
