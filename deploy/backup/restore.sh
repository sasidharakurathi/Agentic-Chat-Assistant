#!/usr/bin/env sh
# Restore a backup over a stack (task 6.7). REPLACES the database.
#
#   deploy/backup/restore.sh backups/2026-09-30T14-00-00Z
#
# It asks you to type the database's name before it does anything
# (RESTORE_CONFIRM=<name> answers for a script). Then:
#   1. stops the API and the worker, so nothing writes during the restore;
#   2. drops and recreates the database and loads db.dump into it;
#   3. copies objects/ back into the bucket (adds and overwrites; files
#      that are in the bucket and not in the backup are left alone);
#   4. starts the API and the worker again. The API's preflight then tries
#      APP_KEK against the restored credentials: if it is not the key the
#      backup was made under, the API refuses to start and says so.
#
# The stack's datastores (postgres, minio) must be running. On a new
# machine: fill in .env.production WITH THE ORIGINAL APP_KEK, then
#   docker compose --env-file .env.production -f docker-compose.prod.yml up -d postgres redis minio
# and run this.
#
# See docs/OPERATIONS.md.

. "$(dirname "$0")/lib.sh"

SRC="${1:-}"
[ -n "$SRC" ] && [ -d "$SRC" ] || die "usage: restore.sh <backup folder>"
[ -f "$SRC/manifest.json" ] || die "$SRC has no manifest.json: the backup did not finish"
WANT_SHA="$(sed -n 's/.*"db_dump_sha256": "\([0-9a-f]*\)".*/\1/p' "$SRC/manifest.json")"
[ "$(sha256_of "$SRC/db.dump")" = "$WANT_SHA" ] || die "db.dump does not match its checksum"
BUCKET="$(bucket)"

say "this REPLACES database \"$DB_NAME\" with $SRC"
if [ "${RESTORE_CONFIRM:-}" != "$DB_NAME" ]; then
  printf '[backup] type the database name (%s) to go on: ' "$DB_NAME"
  read -r answer
  [ "$answer" = "$DB_NAME" ] || die "not confirmed; nothing was changed"
fi

if [ "${SKIP_SERVICES:-0}" != "1" ]; then
  say "stopping api and worker"
  $COMPOSE stop api worker >/dev/null 2>&1 || true
fi

say "replacing database $DB_NAME"
psql_in -d postgres -qc "DROP DATABASE IF EXISTS \"$DB_NAME\" WITH (FORCE)"
psql_in -d postgres -qc "CREATE DATABASE \"$DB_NAME\" OWNER \"$DB_USER\""
$COMPOSE exec -T postgres pg_restore -U "$DB_USER" -d "$DB_NAME" --no-owner --exit-on-error \
  < "$SRC/db.dump"
REVISION="$(psql_in -d "$DB_NAME" -Atc 'SELECT version_num FROM alembic_version' | tr -d '\r')"
say "database restored (schema $REVISION)"

if [ "${SKIP_OBJECTS:-0}" != "1" ]; then
  say "files -> bucket $BUCKET"
  mc_run "$SRC/objects" "mc mb --ignore-existing stack/$BUCKET >/dev/null && mc mirror --quiet --overwrite /backup stack/$BUCKET >/dev/null"
fi

if [ "${SKIP_SERVICES:-0}" != "1" ]; then
  say "starting api and worker (the api migrates to this release's schema if the backup is older)"
  $COMPOSE up -d api worker
  say "watch the preflight: $COMPOSE logs -f api"
fi
say "restored: $SRC"
