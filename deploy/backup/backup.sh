#!/usr/bin/env sh
# Back up a running stack (task 6.7): the database and the uploaded files.
#
#   deploy/backup/backup.sh                 # the production stack
#   KEEP=14 deploy/backup/backup.sh         # and delete all but the newest 14
#
# Writes backups/<UTC time>/ :
#   db.dump        pg_dump, custom format (consistent: one snapshot)
#   objects/       the bucket, file for file
#   manifest.json  when, which schema revision, sizes, and db.dump's checksum
#
# The stack keeps running. The dump is one consistent snapshot; the files
# are copied just after it, so a document uploaded in those seconds can be
# in objects/ and not in the dump. Restoring such a backup leaves an
# unused file, never a missing one.
#
# NOT in the backup: APP_KEK and the other secrets in .env.production. The
# database holds credentials encrypted with APP_KEK and cannot be read
# without it. Keep that file somewhere else, and keep it.
#
# See docs/OPERATIONS.md.

. "$(dirname "$0")/lib.sh"

STAMP="$(date -u +%Y-%m-%dT%H-%M-%SZ)"
DEST="$BACKUP_DIR/$STAMP"
BUCKET="$(bucket)"
mkdir -p "$DEST/objects"
# A half-written backup must not look like a backup: it is only given its
# manifest at the very end, and verify.sh and restore.sh refuse one without.
trap 'say "failed: $DEST is incomplete (no manifest.json)"' EXIT

say "database $DB_NAME -> $DEST/db.dump"
$COMPOSE exec -T postgres pg_dump -U "$DB_USER" -d "$DB_NAME" --format=custom --no-owner \
  > "$DEST/db.dump"
[ -s "$DEST/db.dump" ] || die "the dump is empty"
# It must at least be readable as an archive.
$COMPOSE exec -T postgres pg_restore --list < "$DEST/db.dump" > /dev/null \
  || die "the dump cannot be read back"

REVISION="$(psql_in -d "$DB_NAME" -Atc 'SELECT version_num FROM alembic_version' | tr -d '\r')"

say "bucket $BUCKET -> $DEST/objects"
mc_run "$DEST/objects" "mc mirror --quiet --overwrite stack/$BUCKET /backup >/dev/null"
OBJECTS="$(find "$DEST/objects" -type f | wc -l | tr -d ' ')"

cat > "$DEST/manifest.json" <<EOF
{
  "created_at": "$STAMP",
  "database": "$DB_NAME",
  "schema_revision": "$REVISION",
  "bucket": "$BUCKET",
  "objects": $OBJECTS,
  "db_dump_bytes": $(wc -c < "$DEST/db.dump" | tr -d ' '),
  "db_dump_sha256": "$(sha256_of "$DEST/db.dump")"
}
EOF
trap - EXIT
say "done: $DEST (schema $REVISION, $OBJECTS files)"

if [ -n "${KEEP:-}" ]; then
  # Oldest first, all but the newest $KEEP. Only folders this script made:
  # a name that is a UTC time, holding a manifest.
  ls -1d "$BACKUP_DIR"/20??-??-??T??-??-??Z 2>/dev/null | sort | head -n "-$KEEP" | while read -r old; do
    [ -f "$old/manifest.json" ] || continue
    say "removing old backup $old"
    rm -rf "$old"
  done
fi

say "remember: APP_KEK is not in this backup. Without it the stored credentials cannot be read."
