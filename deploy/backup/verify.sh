#!/usr/bin/env sh
# Prove a backup restores (task 6.7), without touching the live database.
#
#   deploy/backup/verify.sh backups/2026-09-30T14-00-00Z
#   deploy/backup/verify.sh            # the newest one
#
# Restores db.dump into a scratch database next to the live one, checks
# that the schema revision and the main tables came back, and drops the
# scratch database again. A backup that has never been restored is a hope,
# not a backup: run this from the same schedule as backup.sh.

. "$(dirname "$0")/lib.sh"

SRC="${1:-$(ls -1d "$BACKUP_DIR"/20??-??-??T??-??-??Z 2>/dev/null | sort | tail -n 1)}"
[ -n "$SRC" ] && [ -d "$SRC" ] || die "no backup found (looked in $BACKUP_DIR)"
[ -f "$SRC/manifest.json" ] || die "$SRC has no manifest.json: the backup did not finish"

WANT_SHA="$(sed -n 's/.*"db_dump_sha256": "\([0-9a-f]*\)".*/\1/p' "$SRC/manifest.json")"
WANT_REV="$(sed -n 's/.*"schema_revision": "\([^"]*\)".*/\1/p' "$SRC/manifest.json")"
WANT_OBJECTS="$(sed -n 's/.*"objects": \([0-9]*\).*/\1/p' "$SRC/manifest.json")"

[ "$(sha256_of "$SRC/db.dump")" = "$WANT_SHA" ] || die "db.dump does not match its checksum: the file changed after the backup"
say "checksum ok"

HAVE_OBJECTS="$(find "$SRC/objects" -type f | wc -l | tr -d ' ')"
[ "$HAVE_OBJECTS" = "$WANT_OBJECTS" ] || die "objects/ holds $HAVE_OBJECTS files, the manifest says $WANT_OBJECTS"
say "files ok ($HAVE_OBJECTS)"

SCRATCH="restore_verify_$(date -u +%s)"
cleanup() { psql_in -d postgres -qc "DROP DATABASE IF EXISTS \"$SCRATCH\"" >/dev/null 2>&1 || true; }
trap cleanup EXIT

say "restoring into scratch database $SCRATCH"
psql_in -d postgres -qc "CREATE DATABASE \"$SCRATCH\""
$COMPOSE exec -T postgres pg_restore -U "$DB_USER" -d "$SCRATCH" --no-owner --exit-on-error \
  < "$SRC/db.dump"

GOT_REV="$(psql_in -d "$SCRATCH" -Atc 'SELECT version_num FROM alembic_version' | tr -d '\r')"
[ "$GOT_REV" = "$WANT_REV" ] || die "schema revision is $GOT_REV, the manifest says $WANT_REV"

COUNTS="$(psql_in -d "$SCRATCH" -Atc "
  SELECT 'users=' || (SELECT count(*) FROM users)
      || ' orgs=' || (SELECT count(*) FROM organizations)
      || ' assistants=' || (SELECT count(*) FROM assistants)
      || ' conversations=' || (SELECT count(*) FROM conversations)
      || ' messages=' || (SELECT count(*) FROM messages)
      || ' chunks=' || (SELECT count(*) FROM chunks)
      || ' secrets=' || (SELECT count(*) FROM secrets)" | tr -d '\r')"
INDEXES="$(psql_in -d "$SCRATCH" -Atc "SELECT count(*) FROM pg_indexes WHERE indexname = 'ix_chunks_embedding_hnsw'" | tr -d '\r')"
[ "$INDEXES" = "1" ] || die "the vector index did not come back"

say "restored: schema $GOT_REV, $COUNTS, vector index present"
say "verified: $SRC"
