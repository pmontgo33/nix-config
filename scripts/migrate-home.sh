#!/usr/bin/env bash
# Home directory migration script
# Usage: ./migrate-home.sh <source-user> <source-host> <dest-path>
#
# This script syncs a user's home directory from a source machine to the current machine,
# excluding common cache/temporary files and large directories.

set -euo pipefail

SOURCE_USER="${1:-patrick}"
SOURCE_HOST="${2:-tesseract}"
DEST_PATH="${3:-/home/patrick/}"

echo "Migrating home directory..."
echo "  Source: ${SOURCE_USER}@${SOURCE_HOST}:/home/${SOURCE_USER}/"
echo "  Destination: ${DEST_PATH}"
echo ""

rsync -avhP --delete \
  --exclude='*/.cache' \
  --exclude='*/.local/share/Trash' \
  --exclude='*/.thumbnails' \
  --exclude='*/Downloads' \
  --exclude='*/Nextcloud' \
  --exclude='*/mnt' \
  --exclude='*/.npm' \
  --exclude='*/.cargo/registry' \
  --exclude='*/.cargo/git' \
  --exclude='*/.rustup/toolchains' \
  --exclude='*/.local/share/Steam' \
  --exclude='*.tmp' \
  --exclude='*.temp' \
  --exclude='node_modules' \
  "${SOURCE_USER}@${SOURCE_HOST}:/home/${SOURCE_USER}/" "$DEST_PATH"

echo ""
echo "Migration complete. Fixing permissions..."
ssh "root@${SOURCE_HOST}" "chown -R ${SOURCE_USER}:${SOURCE_USER} ${DEST_PATH}"
ssh "root@${SOURCE_HOST}" "rm -f ${DEST_PATH}/.mozilla/firefox/*/lock ${DEST_PATH}/.mozilla/firefox/.parentlock"

echo "Done!"
