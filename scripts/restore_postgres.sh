#!/bin/sh
set -eu

if [ "$#" -lt 1 ] || [ "$#" -gt 2 ]; then
  echo "usage: scripts/restore_postgres.sh BACKUP [DATABASE_restore_test]" >&2
  exit 2
fi
backup=$1
target=${2:-flowmate_restore_test}
case "$target" in
  *_restore_test) ;;
  *) echo "restore target must end in _restore_test" >&2; exit 2 ;;
esac
test -f "$backup"

manifest="$backup.json"
if [ -f "$manifest" ]; then
  expected=$(sed -n 's/.*"sha256":"\([0-9a-f]*\)".*/\1/p' "$manifest")
  if command -v sha256sum >/dev/null 2>&1; then
    actual=$(sha256sum "$backup" | awk '{print $1}')
  else
    actual=$(shasum -a 256 "$backup" | awk '{print $1}')
  fi
  test -n "$expected" && test "$expected" = "$actual"
fi

docker compose -f docker-compose.test.yml up -d --wait postgres-test
docker compose -f docker-compose.test.yml exec -T postgres-test \
  dropdb -U flowmate_test --if-exists "$target"
docker compose -f docker-compose.test.yml exec -T postgres-test \
  createdb -U flowmate_test "$target"
docker compose -f docker-compose.test.yml exec -T postgres-test \
  pg_restore -U flowmate_test -d "$target" --no-owner --no-privileges < "$backup"

port=${TEST_POSTGRES_PORT:-5433}
DATABASE_URL="postgresql+asyncpg://flowmate_test:flowmate_test@localhost:$port/$target" \
  uv run alembic current | grep -q '0020_stage8_stabilization'
docker compose -f docker-compose.test.yml exec -T postgres-test \
  psql -U flowmate_test -d "$target" -Atqc \
  "SELECT count(*) >= 0 FROM users; SELECT count(*) >= 0 FROM alembic_version;" \
  | grep -q t
printf 'restore verified: %s\n' "$target"
