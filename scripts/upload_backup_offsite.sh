#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

ROOT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
HELPER="$ROOT_DIR/scripts/backup_artifact.py"
REMOTE=${FLOWMATE_OFFSITE_REMOTE:-}
RECIPIENTS_FILE=${FLOWMATE_AGE_RECIPIENTS_FILE:-}
TEMP_DIR=""
PG_CLIENT_IMAGE=${FLOWMATE_PG_CLIENT_IMAGE:-postgres:16-bookworm}

die() {
  printf 'error: %s\n' "$*" >&2
  exit 1
}

cleanup() {
  if [[ -n "$TEMP_DIR" && -d "$TEMP_DIR" ]]; then
    rm -rf -- "$TEMP_DIR"
  fi
}
trap cleanup EXIT
trap 'exit 130' HUP INT TERM

check_archive() {
  local archive=$1
  if command -v pg_restore >/dev/null 2>&1 && pg_restore --list "$archive" >/dev/null 2>&1; then
    return 0
  fi
  command -v docker >/dev/null 2>&1 || return 1
  docker run --rm -i "$PG_CLIENT_IMAGE" pg_restore --list <"$archive" >/dev/null
}

(( $# == 1 )) || die "usage: scripts/upload_backup_offsite.sh BACKUP"
BACKUP=$1
MANIFEST="$BACKUP.json"
[[ -n "$REMOTE" ]] || die "FLOWMATE_OFFSITE_REMOTE is required"
[[ -n "$RECIPIENTS_FILE" ]] || die "FLOWMATE_AGE_RECIPIENTS_FILE is required"
[[ -f "$RECIPIENTS_FILE" && -r "$RECIPIENTS_FILE" && ! -L "$RECIPIENTS_FILE" ]] \
  || die "age recipients file must be a regular non-symlink file"
for command_name in age rclone python3; do
  command -v "$command_name" >/dev/null 2>&1 || die "$command_name is required"
done

python3 "$HELPER" validate-dump-manifest \
  --artifact "$BACKUP" --manifest "$MANIFEST"
check_archive "$BACKUP" || die "backup is not a readable pg_dump archive"

TEMP_DIR=$(mktemp -d "${TMPDIR:-/tmp}/flowmate-offsite-upload.XXXXXX")
chmod 0700 "$TEMP_DIR"
bundle="$TEMP_DIR/$(basename "$BACKUP").tar"
ciphertext="$bundle.age"
external_manifest="$ciphertext.json"

python3 "$HELPER" pack-bundle \
  --artifact "$BACKUP" --manifest "$MANIFEST" --output "$bundle"
age --encrypt --recipients-file "$RECIPIENTS_FILE" --output "$ciphertext" "$bundle"
rm -f -- "$bundle"
python3 "$HELPER" create-offsite-manifest \
  --artifact "$ciphertext" --output "$external_manifest"

remote_ciphertext="${REMOTE%/}/$(basename "$ciphertext")"
remote_manifest="$remote_ciphertext.json"
rclone copyto "$ciphertext" "$remote_ciphertext"
rclone copyto "$external_manifest" "$remote_manifest"

mkdir -m 0700 "$TEMP_DIR/verified"
downloaded="$TEMP_DIR/verified/$(basename "$ciphertext")"
downloaded_manifest="$downloaded.json"
rclone copyto "$remote_ciphertext" "$downloaded"
rclone copyto "$remote_manifest" "$downloaded_manifest"
python3 "$HELPER" validate-offsite-manifest \
  --artifact "$downloaded" --manifest "$downloaded_manifest"

printf 'Encrypted backup uploaded and verified: %s\n' "$remote_ciphertext"
