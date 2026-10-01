#!/usr/bin/env sh
set -e

# Check the settings and what they point at before anything else (task
# 6.6): a deployment that is going to fail fails here, in a few readable
# lines. PREFLIGHT=0 skips it.
if [ "${PREFLIGHT:-1}" = "1" ]; then
  python -m app.preflight
fi

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
