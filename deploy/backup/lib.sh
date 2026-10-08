# Shared by backup.sh, verify.sh and restore.sh (task 6.7). Sourced, not run.
#
# Settings, all optional, from the environment:
#   COMPOSE       how to address the stack. Default: the production file
#                 with .env.production, from the repo root.
#   BACKUP_DIR    where backups are kept. Default: ./backups
#   DB_NAME       the database. Default: app
#   DB_USER       its owner. Default: app
#   S3_BUCKET     the bucket. Default: what the env file says, else
#                 assistant-uploads
#
# Nothing here prints a secret. MinIO's credentials are read from the
# running MinIO container into shell variables and handed to a one-off
# `mc` container through its environment.

set -eu

# Git Bash on Windows rewrites arguments that look like Unix paths before
# docker sees them. Harmless everywhere else.
export MSYS_NO_PATHCONV=1

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$ROOT"

if [ -z "${COMPOSE:-}" ]; then
  COMPOSE="docker compose --env-file .env.production -f docker-compose.prod.yml"
fi
BACKUP_DIR="${BACKUP_DIR:-$ROOT/backups}"
DB_NAME="${DB_NAME:-app}"
DB_USER="${DB_USER:-app}"

say() { printf '[backup] %s\n' "$*"; }
die() { printf '[backup] ERROR: %s\n' "$*" >&2; exit 1; }

# The bucket: from the environment, else from the stack's env file, else
# the default.
bucket() {
  if [ -n "${S3_BUCKET:-}" ]; then
    printf '%s' "$S3_BUCKET"
  elif [ -f .env.production ] && grep -q '^S3_BUCKET=.' .env.production; then
    grep '^S3_BUCKET=' .env.production | head -n 1 | cut -d= -f2-
  else
    printf 'assistant-uploads'
  fi
}

# A path docker can mount: on Windows (Git Bash) the Windows form.
mountable() {
  if command -v cygpath >/dev/null 2>&1; then cygpath -w "$1"; else printf '%s' "$1"; fi
}

psql_in() { $COMPOSE exec -T postgres psql -U "$DB_USER" -v ON_ERROR_STOP=1 "$@"; }

# Run `mc` in a one-off container on the stack's network, with a host
# folder at /backup. The MinIO image has `mc` but no `tar`, and an `exec`
# cannot mount a folder, so the objects travel through this container.
# It runs as whoever runs this script, so the files it writes are theirs:
# the image's own user (65532) could not write to their folder on Linux.
# HOME is moved to /tmp because `mc` keeps its settings there.
mc_run() {
  host_dir="$1"
  shift
  user="$($COMPOSE exec -T minio printenv MINIO_ROOT_USER | tr -d '\r')"
  pass="$($COMPOSE exec -T minio printenv MINIO_ROOT_PASSWORD | tr -d '\r')"
  MC_USER="$user" MC_PASS="$pass" $COMPOSE run --rm --no-deps -T \
    --user "$(id -u):$(id -g)" -e HOME=/tmp \
    -e MC_USER -e MC_PASS \
    -v "$(mountable "$host_dir"):/backup" \
    --entrypoint /bin/sh minio-init -c \
    'mc alias set stack http://minio:9000 "$MC_USER" "$MC_PASS" >/dev/null && '"$*"
}

# From standard input: given a file name with a backslash in it (Git Bash on
# Windows), sha256sum escapes its output and the checksum gains a "\".
sha256_of() {
  if command -v sha256sum >/dev/null 2>&1; then sha256sum < "$1" | cut -d' ' -f1
  else shasum -a 256 < "$1" | cut -d' ' -f1
  fi
}
