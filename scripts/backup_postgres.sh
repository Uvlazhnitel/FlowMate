#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

ROOT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
BACKUP_DIR=${BACKUP_DIR:-"$ROOT_DIR/backups"}
COMPOSE_FILE=${FLOWMATE_DB_COMPOSE_FILE:-"$ROOT_DIR/docker-compose.yml"}
DB_SERVICE=${FLOWMATE_DB_SERVICE:-postgres}
DB_NAME=${FLOWMATE_BACKUP_DATABASE:-}
DB_USER=${FLOWMATE_BACKUP_USER:-}
TIMESTAMP=${FLOWMATE_BACKUP_TIMESTAMP:-$(date -u +%Y%m%dT%H%M%SZ)}
DAILY_NAME="flowmate-daily-${TIMESTAMP}.dump"
DAILY_PATH="$BACKUP_DIR/$DAILY_NAME"
MANIFEST_HELPER="$ROOT_DIR/scripts/backup_artifact.py"
TEMP_DIR=""

cleanup() {
  if [[ -n "$TEMP_DIR" && -d "$TEMP_DIR" ]]; then
    rm -rf -- "$TEMP_DIR"
  fi
}
trap cleanup EXIT
trap 'exit 130' HUP INT TERM

die() {
  printf 'error: %s\n' "$*" >&2
  exit 1
}

(( $# == 0 )) || die "usage: scripts/backup_postgres.sh"
[[ "$TIMESTAMP" =~ ^[0-9]{8}T[0-9]{6}Z$ ]] || die "invalid backup timestamp"
[[ -f "$COMPOSE_FILE" ]] || die "Compose file does not exist: $COMPOSE_FILE"
command -v docker >/dev/null 2>&1 || die "docker is required"
command -v python3 >/dev/null 2>&1 || die "python3 is required"

mkdir -p -- "$BACKUP_DIR"
[[ -d "$BACKUP_DIR" && ! -L "$BACKUP_DIR" ]] \
  || die "backup directory must be a real directory"
chmod 0700 "$BACKUP_DIR"
[[ ! -e "$DAILY_PATH" && ! -L "$DAILY_PATH" ]] || die "backup already exists: $DAILY_PATH"
[[ ! -e "$DAILY_PATH.json" && ! -L "$DAILY_PATH.json" ]] || die "manifest already exists: $DAILY_PATH.json"
TEMP_DIR=$(mktemp -d "$BACKUP_DIR/.flowmate-backup.XXXXXX")
TEMP_DUMP="$TEMP_DIR/$DAILY_NAME"
TEMP_MANIFEST="$TEMP_DIR/$DAILY_NAME.json"

compose=(docker compose -f "$COMPOSE_FILE")
if [[ -z "$DB_NAME" ]]; then
  DB_NAME=$("${compose[@]}" exec -T "$DB_SERVICE" printenv POSTGRES_DB)
fi
if [[ -z "$DB_USER" ]]; then
  DB_USER=$("${compose[@]}" exec -T "$DB_SERVICE" printenv POSTGRES_USER)
fi
[[ "$DB_NAME" =~ ^[A-Za-z0-9_]+$ ]] || die "invalid database name"
[[ "$DB_USER" =~ ^[A-Za-z0-9_]+$ ]] || die "invalid database user"

postgres_version=$("${compose[@]}" exec -T "$DB_SERVICE" \
  psql -X -v ON_ERROR_STOP=1 -At -U "$DB_USER" -d "$DB_NAME" \
  -c 'SHOW server_version;')
pg_dump_version=$("${compose[@]}" exec -T "$DB_SERVICE" pg_dump --version)
revisions=()
while IFS= read -r revision; do
  revisions+=("$revision")
done < <("${compose[@]}" exec -T "$DB_SERVICE" \
  psql -X -v ON_ERROR_STOP=1 -At -U "$DB_USER" -d "$DB_NAME" \
  -c 'SELECT version_num FROM alembic_version ORDER BY version_num;')
(( ${#revisions[@]} > 0 )) || die "database has no Alembic revision"

"${compose[@]}" exec -T "$DB_SERVICE" pg_dump \
  -U "$DB_USER" -d "$DB_NAME" --format=custom --compress=9 --no-owner --no-acl \
  >"$TEMP_DUMP"
"${compose[@]}" exec -T "$DB_SERVICE" pg_restore --list <"$TEMP_DUMP" >/dev/null

manifest_args=(
  create-dump-manifest
  --artifact "$TEMP_DUMP"
  --output "$TEMP_MANIFEST"
  --postgres-version "$postgres_version"
  --pg-dump-version "$pg_dump_version"
)
for revision in "${revisions[@]}"; do
  manifest_args+=(--revision "$revision")
done
python3 "$MANIFEST_HELPER" "${manifest_args[@]}"

mv -- "$TEMP_DUMP" "$DAILY_PATH"
mv -- "$TEMP_MANIFEST" "$DAILY_PATH.json"

if [[ $(date -u +%u) == 7 ]]; then
  WEEKLY_NAME="flowmate-weekly-${TIMESTAMP}.dump"
  WEEKLY_PATH="$BACKUP_DIR/$WEEKLY_NAME"
  TEMP_WEEKLY="$TEMP_DIR/$WEEKLY_NAME"
  TEMP_WEEKLY_MANIFEST="$TEMP_DIR/$WEEKLY_NAME.json"
  [[ ! -e "$WEEKLY_PATH" && ! -L "$WEEKLY_PATH" ]] || die "weekly backup already exists"
  [[ ! -e "$WEEKLY_PATH.json" && ! -L "$WEEKLY_PATH.json" ]] \
    || die "weekly manifest already exists"
  cp -- "$DAILY_PATH" "$TEMP_WEEKLY"
  weekly_manifest_args=(
    create-dump-manifest
    --artifact "$TEMP_WEEKLY"
    --output "$TEMP_WEEKLY_MANIFEST"
    --postgres-version "$postgres_version"
    --pg-dump-version "$pg_dump_version"
  )
  for revision in "${revisions[@]}"; do
    weekly_manifest_args+=(--revision "$revision")
  done
  python3 "$MANIFEST_HELPER" "${weekly_manifest_args[@]}"
  mv -- "$TEMP_WEEKLY" "$WEEKLY_PATH"
  mv -- "$TEMP_WEEKLY_MANIFEST" "$WEEKLY_PATH.json"
fi

python3 - "$BACKUP_DIR" <<'PY'
from pathlib import Path
import sys

directory = Path(sys.argv[1])
for pattern, keep in (("flowmate-daily-*.dump", 7), ("flowmate-weekly-*.dump", 4)):
    for dump in sorted(directory.glob(pattern), reverse=True)[keep:]:
        manifest = Path(f"{dump}.json")
        dump.unlink(missing_ok=True)
        manifest.unlink(missing_ok=True)
PY

printf 'Backup created: %s\n' "$DAILY_PATH"
