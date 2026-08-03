#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

ROOT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
HELPER="$ROOT_DIR/scripts/backup_artifact.py"
IDENTITY_FILE=${FLOWMATE_AGE_IDENTITY_FILE:-}
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

(( $# >= 1 && $# <= 2 )) \
  || die "usage: scripts/restore_backup_offsite.sh REMOTE_CIPHERTEXT [DATABASE_restore_test]"
REMOTE_CIPHERTEXT=$1
TARGET_DB=${2:-flowmate_restore_test}
[[ "$REMOTE_CIPHERTEXT" == *.tar.age ]] || die "remote artifact must end in .tar.age"
[[ -n "$IDENTITY_FILE" ]] || die "FLOWMATE_AGE_IDENTITY_FILE is required"
[[ -f "$IDENTITY_FILE" && -r "$IDENTITY_FILE" && ! -L "$IDENTITY_FILE" ]] \
  || die "age identity file must be a regular non-symlink file"
for command_name in age rclone python3; do
  command -v "$command_name" >/dev/null 2>&1 || die "$command_name is required"
done

TEMP_DIR=$(mktemp -d "${TMPDIR:-/tmp}/flowmate-offsite-restore.XXXXXX")
chmod 0700 "$TEMP_DIR"
ciphertext="$TEMP_DIR/$(basename "$REMOTE_CIPHERTEXT")"
external_manifest="$ciphertext.json"
rclone copyto "$REMOTE_CIPHERTEXT" "$ciphertext"
rclone copyto "$REMOTE_CIPHERTEXT.json" "$external_manifest"
python3 "$HELPER" validate-offsite-manifest \
  --artifact "$ciphertext" --manifest "$external_manifest"

bundle="$TEMP_DIR/decrypted.tar"
age --decrypt --identity "$IDENTITY_FILE" --output "$bundle" "$ciphertext"
plaintext_dir="$TEMP_DIR/plaintext"
mkdir -m 0700 "$plaintext_dir"
backup=$(python3 "$HELPER" extract-bundle \
  --bundle "$bundle" --output-dir "$plaintext_dir")
rm -f -- "$bundle"
check_archive "$backup" || die "decrypted backup is not readable"

"$ROOT_DIR/scripts/restore_postgres.sh" "$backup" "$TARGET_DB"
printf 'Encrypted backup restored and verified: %s\n' "$REMOTE_CIPHERTEXT"
