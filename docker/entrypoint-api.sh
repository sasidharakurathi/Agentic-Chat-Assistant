#!/usr/bin/env sh
set -e

# Apply DB migrations before starting (idempotent).
if [ "${RUN_MIGRATIONS:-1}" = "1" ]; then
  echo "[entrypoint] alembic upgrade head"
  python -m alembic upgrade head
fi

if [ "${RUN_SEED:-0}" = "1" ]; then
  echo "[entrypoint] seeding demo data"
  python -m scripts.seed || true
fi

exec "$@"
