#!/usr/bin/env bash
# Wrapper for the Nix-managed family calendar publisher.
set -euo pipefail

LOCKFILE="/var/lib/hermes/.hermes/workspace/.family-calendar-router.lock"
TIMEOUT_MINUTES=10
FLOCK="${FLOCK:-flock}"
TIMEOUT="${TIMEOUT:-timeout}"
PYTHON="${PYTHON:-python3}"
MKDIR="${MKDIR:-mkdir}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Cron scheduler runs with a minimal PATH — ensure subprocesses can find
# system binaries (curl, scp, ssh, etc.)
export PATH="${PATH:-/run/current-system/sw/bin}"

$MKDIR -p "$(dirname "$LOCKFILE")"
exec 200>"$LOCKFILE"
$FLOCK -n 200 || {
    LOCK_AGE=$(stat -c "%Y" "$LOCKFILE" 2>/dev/null)
    NOW=$(date +%s)
    if [[ -n "$LOCK_AGE" ]]; then
        AGE_MIN=$(( (NOW - LOCK_AGE) / 60 ))
        echo "Another instance is running (lock held for ~${AGE_MIN}m). Exiting."
    else
        echo "Another instance is running, exiting."
    fi
    exit 0
}

exec "$TIMEOUT" "${TIMEOUT_MINUTES}m" \
    "$PYTHON" \
    "$SCRIPT_DIR/router.py" "$@"
