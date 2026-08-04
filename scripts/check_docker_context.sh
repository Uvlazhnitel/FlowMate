#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

ROOT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
PYTHON_IMAGE=${FLOWMATE_CONTEXT_CHECK_IMAGE:-python:3.12-slim-bookworm@sha256:d50fb7611f86d04a3b0471b46d7557818d88983fc3136726336b2a4c657aa30b}
TEMP_DIR=""
TAGS=()

cleanup() {
  local tag
  for tag in "${TAGS[@]}"; do
    docker image rm --force "$tag" >/dev/null 2>&1 || true
  done
  if [[ -n "$TEMP_DIR" && -d "$TEMP_DIR" ]]; then
    rm -rf -- "$TEMP_DIR"
  fi
}
trap cleanup EXIT
trap 'exit 130' HUP INT TERM

command -v docker >/dev/null 2>&1 || {
  printf 'error: docker is required for build context checks\n' >&2
  exit 1
}

TEMP_DIR=$(mktemp -d "${TMPDIR:-/tmp}/flowmate-context-check.XXXXXX")

check_context() {
  local name=$1
  local ignore_file=$2
  local context="$TEMP_DIR/$name"
  local tag="flowmate-context-check-${name}-$$"
  TAGS+=("$tag")

  mkdir -p "$context/backups/nested" "$context/nested/backups" "$context/nested/config"
  cp -- "$ignore_file" "$context/.dockerignore"
  printf 'public\n' >"$context/public-marker.txt"
  printf 'secret\n' >"$context/.env"
  printf 'secret\n' >"$context/.env.production"
  printf 'secret\n' >"$context/backups/nested/backup.dump"
  printf 'secret\n' >"$context/nested/backups/backup.dump.json"
  printf 'secret\n' >"$context/nested/.env.local"
  printf 'secret\n' >"$context/nested/private.dump"
  printf 'secret\n' >"$context/nested/private.dump.json"
  printf 'secret\n' >"$context/nested/recording.ogg"
  printf 'secret\n' >"$context/nested/config/rclone-production.conf"
  printf 'secret\n' >"$context/nested/config/flowmate-age-identity-ci.txt"
  printf 'secret\n' >"$context/nested/config/private.key"
  printf 'secret\n' >"$context/voice.ogg"
  printf 'secret\n' >"$context/plaintext.tar"
  printf 'secret\n' >"$context/encrypted.tar.age"
  printf 'secret\n' >"$context/rclone.conf"
  printf 'secret\n' >"$context/age-identity.txt"
  printf 'secret\n' >"$context/private.pem"
  printf 'secret\n' >"$context/private.key"
  printf 'secret\n' >"$context/application.log"
  cat >"$context/Dockerfile" <<EOF
FROM $PYTHON_IMAGE
COPY . /context
RUN test -f /context/public-marker.txt \\
 && test ! -e /context/.env \\
 && test ! -e /context/.env.production \\
 && test ! -e /context/backups \\
 && test ! -e /context/nested/backups \\
 && test ! -e /context/nested/.env.local \\
 && test ! -e /context/nested/private.dump \\
 && test ! -e /context/nested/private.dump.json \\
 && test ! -e /context/nested/recording.ogg \\
 && test ! -e /context/nested/config/rclone-production.conf \\
 && test ! -e /context/nested/config/flowmate-age-identity-ci.txt \\
 && test ! -e /context/nested/config/private.key \\
 && test ! -e /context/voice.ogg \\
 && test ! -e /context/plaintext.tar \\
 && test ! -e /context/encrypted.tar.age \\
 && test ! -e /context/rclone.conf \\
 && test ! -e /context/age-identity.txt \\
 && test ! -e /context/private.pem \\
 && test ! -e /context/private.key \\
 && test ! -e /context/application.log
EOF
  docker build --quiet --tag "$tag" "$context" >/dev/null
  printf 'Docker build context privacy verified: %s\n' "$name"
}

check_context root "$ROOT_DIR/.dockerignore"
check_context web "$ROOT_DIR/apps/web/.dockerignore"
