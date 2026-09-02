# apps/api — Assistant Studio backend

FastAPI + async SQLAlchemy 2.0 + Alembic. Python 3.11+ (3.12 in CI/Docker).

## Layout

```
app/
  config.py          pydantic-settings; boot-time secret validation
  logging.py         structlog (JSON in prod), request-id contextvar
  db/                engine/session, Base + mixins, migrations/ (Alembic)
  models/            SQLAlchemy models (identity/tenancy in Phase 0)
  schemas/           Pydantic request/response models
  security/          passwords (argon2id), tokens (JWT access + rotating refresh)
  services/          auth, orgs, audit  (business logic; no FastAPI imports)
  api/               errors, middleware, deps (RBAC), routes/
  graph/             graph model + graph→AssistantConfig compiler (Phase 1)
  agent/  rag/  datasources/  mcp/  billing/  evals/   (later phases)
scripts/seed.py      idempotent dev seed
tests/               pytest (async), SQLite; `-m 'not integration'` by default
```

## Run locally (from the repo root)

```bash
pip install -e "apps/api[dev]"
export DATABASE_URL=postgresql+asyncpg://app:app@localhost:5432/app   # or sqlite+aiosqlite:///./dev.db
python -m alembic -c apps/api/alembic.ini upgrade head
(cd apps/api && python -m scripts.seed)
python -m uvicorn app.main:app --app-dir apps/api --reload
```

Docs: `/docs`. Health: `/healthz`, `/readyz`.

## Migrations

```bash
python -m alembic -c apps/api/alembic.ini revision --autogenerate -m "add X"   # review it!
python -m alembic -c apps/api/alembic.ini upgrade head
python -m alembic -c apps/api/alembic.ini downgrade -1
```

`alembic.ini` uses `%(here)s` so it works from any CWD. `env.py` pulls the URL from
`app.config.settings`; SQLite gets `render_as_batch=True` automatically.
