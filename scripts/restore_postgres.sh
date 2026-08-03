#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

ROOT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
COMPOSE_FILE=${FLOWMATE_DB_COMPOSE_FILE:-"$ROOT_DIR/docker-compose.test.yml"}
DB_SERVICE=${FLOWMATE_DB_SERVICE:-postgres-test}
DB_USER=${FLOWMATE_RESTORE_USER:-flowmate_test}
DB_PASSWORD=${FLOWMATE_RESTORE_PASSWORD:-flowmate_test}
DB_HOST=${FLOWMATE_RESTORE_HOST:-localhost}
DB_PORT=${FLOWMATE_RESTORE_PORT:-${TEST_POSTGRES_PORT:-5433}}
ALEMBIC_CONFIG=${ALEMBIC_CONFIG:-"$ROOT_DIR/alembic.ini"}
MANIFEST_HELPER="$ROOT_DIR/scripts/backup_artifact.py"
PG_CLIENT_IMAGE=${FLOWMATE_PG_CLIENT_IMAGE:-postgres:16-bookworm}
STAGING_DB=""
PREVIOUS_DB=""
TARGET_DB=""

usage() {
  printf 'usage: scripts/restore_postgres.sh BACKUP [DATABASE_restore_test]\n' >&2
  exit 2
}

die() {
  printf 'error: %s\n' "$*" >&2
  exit 1
}

compose() {
  docker compose -f "$COMPOSE_FILE" "$@"
}

check_archive() {
  local archive=$1
  if command -v pg_restore >/dev/null 2>&1 && pg_restore --list "$archive" >/dev/null 2>&1; then
    return 0
  fi
  docker run --rm -i "$PG_CLIENT_IMAGE" pg_restore --list <"$archive" >/dev/null
}

db_exists() {
  compose exec -T "$DB_SERVICE" psql -X -v ON_ERROR_STOP=1 -At \
    -U "$DB_USER" -d postgres \
    -c "SELECT 1 FROM pg_database WHERE datname = '$1';" | grep -qx 1
}

disconnect_database() {
  compose exec -T "$DB_SERVICE" psql -X -v ON_ERROR_STOP=1 -U "$DB_USER" \
    -d postgres -c \
    "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = '$1' AND pid <> pg_backend_pid();" \
    >/dev/null
}

drop_database() {
  local database=$1
  if db_exists "$database"; then
    disconnect_database "$database"
    compose exec -T "$DB_SERVICE" dropdb -U "$DB_USER" "$database"
  fi
}

rename_database() {
  local source=$1
  local destination=$2
  disconnect_database "$source"
  compose exec -T "$DB_SERVICE" psql -X -v ON_ERROR_STOP=1 -U "$DB_USER" \
    -d postgres -c "ALTER DATABASE \"$source\" RENAME TO \"$destination\";" \
    >/dev/null
}

database_url() {
  python3 - "$DB_USER" "$DB_PASSWORD" "$DB_HOST" "$DB_PORT" "$1" <<'PY'
import sys
from urllib.parse import quote

user, password, host, port, database = sys.argv[1:]
print(
    f"postgresql+asyncpg://{quote(user, safe='')}:{quote(password, safe='')}"
    f"@{host}:{port}/{quote(database, safe='')}"
)
PY
}

database_revisions() {
  local database=$1
  DATABASE_URL=$(database_url "$database") \
    uv run alembic -c "$ALEMBIC_CONFIG" current 2>&1 \
    | python3 "$MANIFEST_HELPER" parse-alembic-revisions
}

smoke_database() {
  local database=$1
  local actual_revisions
  if ! actual_revisions=$(database_revisions "$database"); then
    printf 'Expected Alembic revisions:\n%s\n' "$PROJECT_REVISIONS" >&2
    printf 'Actual Alembic revisions:\n<unavailable>\n' >&2
    return 1
  fi
  if [[ "$actual_revisions" != "$PROJECT_REVISIONS" ]]; then
    printf 'Expected Alembic revisions:\n%s\n' "$PROJECT_REVISIONS" >&2
    printf 'Actual Alembic revisions:\n%s\n' "$actual_revisions" >&2
    return 1
  fi
  compose exec -T "$DB_SERVICE" psql -X -v ON_ERROR_STOP=1 -At \
    -U "$DB_USER" -d "$database" -c \
    "SELECT to_regclass('public.users') IS NOT NULL
       AND to_regclass('public.notes') IS NOT NULL
       AND to_regclass('public.work_items') IS NOT NULL
       AND to_regclass('public.alembic_version') IS NOT NULL;" \
    | grep -qx t
}

cleanup() {
  local status=$?
  trap - EXIT HUP INT TERM
  if [[ -n "$STAGING_DB" ]] && command -v docker >/dev/null 2>&1; then
    drop_database "$STAGING_DB" >/dev/null 2>&1 || true
  fi
  exit "$status"
}
trap cleanup EXIT
trap 'exit 130' HUP INT TERM

(( $# >= 1 && $# <= 2 )) || usage
BACKUP=$1
MANIFEST="$BACKUP.json"
TARGET_DB=${2:-flowmate_restore_test}

[[ "$TARGET_DB" =~ ^[a-z][a-z0-9_]{0,39}_restore_test$ ]] \
  || die "restore target must be a safe PostgreSQL name ending in _restore_test"
[[ -f "$COMPOSE_FILE" ]] || die "Compose file does not exist: $COMPOSE_FILE"
[[ -f "$ALEMBIC_CONFIG" ]] || die "Alembic config does not exist: $ALEMBIC_CONFIG"
for command_name in docker python3 uv; do
  command -v "$command_name" >/dev/null 2>&1 || die "$command_name is required"
done

# All artifact and project checks happen before Compose starts or target state is read.
MANIFEST_REVISIONS=$(python3 "$MANIFEST_HELPER" validate-dump-manifest \
  --artifact "$BACKUP" --manifest "$MANIFEST" --print-revisions)
check_archive "$BACKUP" || die "backup is not a readable pg_dump archive"
PROJECT_REVISIONS=$(uv run alembic -c "$ALEMBIC_CONFIG" heads 2>&1 \
  | python3 "$MANIFEST_HELPER" parse-alembic-revisions)
if [[ $(printf '%s\n' "$PROJECT_REVISIONS" | wc -l | tr -d ' ') != 1 ]]; then
  printf 'Expected exactly one project Alembic head, found:\n%s\n' \
    "$PROJECT_REVISIONS" >&2
  exit 1
fi
if [[ "$MANIFEST_REVISIONS" != "$PROJECT_REVISIONS" ]]; then
  printf 'Expected project Alembic revisions:\n%s\n' "$PROJECT_REVISIONS" >&2
  printf 'Manifest Alembic revisions:\n%s\n' "$MANIFEST_REVISIONS" >&2
  exit 1
fi

compose up -d --wait "$DB_SERVICE"
base=${TARGET_DB%_restore_test}
for _ in 1 2 3 4 5; do
  nonce="$$_${RANDOM}"
  STAGING_DB="${base}_stage_${nonce}"
  PREVIOUS_DB="${base}_previous_${nonce}"
  [[ ${#STAGING_DB} -le 63 && ${#PREVIOUS_DB} -le 63 ]] \
    || die "generated database name is too long"
  if ! db_exists "$STAGING_DB" && ! db_exists "$PREVIOUS_DB"; then
    break
  fi
  STAGING_DB=""
  PREVIOUS_DB=""
done
[[ -n "$STAGING_DB" ]] || die "could not allocate unique staging database names"

compose exec -T "$DB_SERVICE" createdb -U "$DB_USER" "$STAGING_DB"
compose exec -T "$DB_SERVICE" pg_restore -U "$DB_USER" -d "$STAGING_DB" \
  --single-transaction --exit-on-error --no-owner --no-privileges <"$BACKUP"
smoke_database "$STAGING_DB" || die "staging database verification failed"

had_target=false
if db_exists "$TARGET_DB"; then
  had_target=true
  rename_database "$TARGET_DB" "$PREVIOUS_DB"
fi
if ! rename_database "$STAGING_DB" "$TARGET_DB"; then
  if [[ "$had_target" == true ]]; then
    rename_database "$PREVIOUS_DB" "$TARGET_DB" \
      || die "cutover failed and previous target rollback also failed"
  fi
  die "cutover failed before staging became the target"
fi
STAGING_DB=""

if ! smoke_database "$TARGET_DB"; then
  failed_db="${base}_failed_${nonce}"
  rollback_ok=true
  rename_database "$TARGET_DB" "$failed_db" || rollback_ok=false
  if [[ "$had_target" == true ]]; then
    rename_database "$PREVIOUS_DB" "$TARGET_DB" || rollback_ok=false
  fi
  drop_database "$failed_db" || true
  if [[ "$rollback_ok" != true ]]; then
    die "final verification failed and automatic target rollback was incomplete"
  fi
  die "final restore verification failed; previous target was restored"
fi

if [[ "$had_target" == true ]]; then
  drop_database "$PREVIOUS_DB"
fi
printf 'restore verified: %s\n' "$TARGET_DB"
