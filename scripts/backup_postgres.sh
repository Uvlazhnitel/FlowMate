#!/bin/sh
set -eu

backup_dir=${BACKUP_DIR:-backups}
timestamp=$(date -u +%Y%m%dT%H%M%SZ)
mkdir -p "$backup_dir"
chmod 700 "$backup_dir"

daily="$backup_dir/flowmate-daily-$timestamp.dump"
temporary="$daily.tmp"
trap 'rm -f "$temporary"' EXIT HUP INT TERM

docker compose exec -T postgres sh -c \
  'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" --format=custom --compress=gzip:9' \
  > "$temporary"
test -s "$temporary"
mv "$temporary" "$daily"
chmod 600 "$daily"

if command -v sha256sum >/dev/null 2>&1; then
  checksum=$(sha256sum "$daily" | awk '{print $1}')
else
  checksum=$(shasum -a 256 "$daily" | awk '{print $1}')
fi
size=$(wc -c < "$daily" | tr -d ' ')
manifest="$daily.json"
printf '{"created_at":"%s","format":"pg_dump-custom","sha256":"%s","size":%s}\n' \
  "$timestamp" "$checksum" "$size" > "$manifest"
chmod 600 "$manifest"

if [ "$(date -u +%u)" = "7" ]; then
  weekly="$backup_dir/flowmate-weekly-$timestamp.dump"
  cp "$daily" "$weekly"
  cp "$manifest" "$weekly.json"
  chmod 600 "$weekly" "$weekly.json"
fi

ls -1t "$backup_dir"/flowmate-daily-*.dump 2>/dev/null | awk 'NR > 7' | \
  while IFS= read -r old; do rm -f "$old" "$old.json"; done
ls -1t "$backup_dir"/flowmate-weekly-*.dump 2>/dev/null | awk 'NR > 4' | \
  while IFS= read -r old; do rm -f "$old" "$old.json"; done

printf '%s\n' "$daily"
