# Contributing

## Toolchain

- **Python** 3.11+ (CI and Docker images pin 3.12). The backend is a standard PEP 621
  project (`apps/api/pyproject.toml`); use `uv`, `pip`, or `poetry` — commands in docs use
  `pip` for zero extra installs. To use `uv`: `uv sync --project apps/api --extra dev`.
- **Node** 20+ with **npm workspaces** (no pnpm/yarn). Root `npm install` installs
  `apps/web` and `packages/*`.
- **`just`** is the task runner (`justfile`). A `Makefile` mirrors every recipe for
  environments without `just`.

## Local setup

```bash
just setup            # or: make setup
cp .env.example .env   # fill JWT_SECRET at minimum for dev
docker compose up -d postgres redis minio
just migrate
just seed
```

## Before opening a PR

Run `just check` (lint + typecheck + tests). CI runs the same.

- **Python**: `ruff` (lint + format), `mypy` (typed; `disallow_untyped_defs` in `app/`),
  `pytest` (async, `asyncio_mode=auto`). Tests use SQLite; integration tests that need
  Postgres are marked `@pytest.mark.integration`.
- **Web**: `eslint` (`eslint-config-next`), `prettier`, `tsc --noEmit`.

## Conventions

- **Migrations**: one Alembic revision per schema change; `just migration name="..."`
  autogenerates, then review the file. Never edit an applied migration.
- **Settings**: everything configurable goes through `app/config.py` (`pydantic-settings`),
  never `os.environ` directly. Add new keys to `.env.example`.
- **Secrets** never touch logs, traces, or API responses. Use `app/security/` helpers.
- **Tenant scoping**: every query is filtered by `org_id` at the repository layer; add a
  test that cross-org access 404s.
- **Commits / branches / releases** are handled by the core team. Do not add git hooks or
  CI steps that push, tag, or comment on the repo's behalf.

## Architecture decisions

Non-trivial choices get an ADR in `docs/adr/` (`NNNN-title.md`, format in
`docs/adr/0000-template.md`). Link it from the PR.
