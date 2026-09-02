# Assistant Studio — Makefile fallback for environments without `just`.
# On Windows, prefer `just` (POSIX make is usually absent). Override the venv
# bin dir on Windows with:  make test VENV_BIN=.venv/Scripts

VENV_BIN ?= .venv/bin
PY := $(VENV_BIN)/python

.PHONY: help setup setup-api setup-web up down logs api web migrate seed test lint fmt typecheck check

help:
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  %-16s %s\n", $$1, $$2}'

setup-api: ## Create venv + install API (editable, dev deps)
	python -m venv .venv
	$(PY) -m pip install -U pip
	$(PY) -m pip install -e "apps/api[dev]"

setup-web: ## Install JS workspace deps
	npm install

setup: setup-api setup-web ## Full local setup

up: ## Datastores only (postgres, redis, minio)
	docker compose up -d

up-all: ## Datastores + build & run api + web
	docker compose --profile apps up -d --build

down: ## Stop everything
	docker compose --profile apps down

logs: ## Tail compose logs (make logs S=api)
	docker compose logs -f $(S)

api: ## Run the API with autoreload
	$(PY) -m uvicorn app.main:app --app-dir apps/api --reload --host 0.0.0.0 --port 8000

web: ## Run the Next.js dev server
	npm run dev -w web

migrate: ## Apply DB migrations
	$(PY) -m alembic -c apps/api/alembic.ini upgrade head

seed: ## Seed a demo org + admin user
	cd apps/api && ../../$(PY) -m scripts.seed

test: ## Run API tests
	$(PY) -m pytest apps/api -q

lint: ## Lint API + web
	$(PY) -m ruff check apps/api
	$(PY) -m ruff format --check apps/api
	npm run lint -w web

fmt: ## Auto-format API + web
	$(PY) -m ruff format apps/api
	$(PY) -m ruff check --fix apps/api
	npm run format

typecheck: ## Type-check API + web
	cd apps/api && ../../$(PY) -m mypy app scripts
	npm run typecheck -w web

check: lint typecheck test ## Everything CI runs
