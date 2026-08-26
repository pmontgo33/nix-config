#!/run/current-system/sw/bin/bash
# Generate, validate, and publish Philadelphia sports calendars.
set -euo pipefail

PYTHON="${PYTHON:-/run/current-system/sw/bin/python3}"
RSYNC="${RSYNC:-/run/current-system/sw/bin/rsync}"
SSH="${SSH:-/run/current-system/sw/bin/ssh}"
MKDIR="${MKDIR:-/run/current-system/sw/bin/mkdir}"
MKTemp="${MKTemp:-/run/current-system/sw/bin/mktemp}"
MV="${MV:-/run/current-system/sw/bin/mv}"
RM="${RM:-/run/current-system/sw/bin/rm}"
FLOCK="${FLOCK:-/run/current-system/sw/bin/flock}"

LOCK_FILE="/var/lib/hermes/workspace/.philly-sports-publisher.lock"
exec 9>"$LOCK_FILE"
if ! $FLOCK -n 9; then
  echo "Another sports publisher is active; refusing concurrent publication" >&2
  exit 75
fi

GENERATOR_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
OUTPUT_DIR="/var/lib/hermes/workspace/philly-sports-cal/output"
STAGE_ROOT="$($MKTemp -d /var/lib/hermes/workspace/.philly-sports-stage.XXXXXX)"
OLD_OUTPUT=""
REMOTE_ROOT="/var/www/ical/sports"
REMOTE_STAGE="/var/www/ical/.sports-stage-$$"
REMOTE_BACKUP="/var/www/ical/.sports-previous-$$"
REMOTE_PREPARED=0
cleanup() {
  $RM -rf "$STAGE_ROOT"
  if [ -n "$OLD_OUTPUT" ]; then
    $RM -rf "$OLD_OUTPUT"
  fi
  if [ "$REMOTE_PREPARED" -eq 1 ]; then
    $SSH patrick@bifrost \
      "/run/current-system/sw/bin/rm -rf '$REMOTE_STAGE' '$REMOTE_BACKUP'" \
      >/dev/null 2>&1 || true
  fi
}
trap cleanup EXIT

$MKDIR -p "$STAGE_ROOT"

echo "Generating ICS files..."
$PYTHON "$GENERATOR_DIR/generate.py" --output-dir "$STAGE_ROOT"

echo "Validating ICS files..."
$PYTHON "$GENERATOR_DIR/validate.py" \
  "$STAGE_ROOT/flyers.ics" \
  "$STAGE_ROOT/phillies.ics" \
  "$STAGE_ROOT/eagles.ics"

echo "Staging validated feeds on bifrost..."
$SSH patrick@bifrost \
  "set -eu; /run/current-system/sw/bin/rm -rf '$REMOTE_STAGE'; /run/current-system/sw/bin/mkdir -m 0755 '$REMOTE_STAGE'"
REMOTE_PREPARED=1
$RSYNC -avz --checksum --delete --chmod=F644 \
  -e "$SSH" "$STAGE_ROOT/" "patrick@bifrost:$REMOTE_STAGE/"

# Replace the served directory only after all three staged files are present.
# If the directory swap or permission fix fails, restore the previous release.
$SSH patrick@bifrost "set -eu
had_previous=0
new_release=0
rollback() {
  status=\$?
  trap - EXIT
  if [ \"\$status\" -ne 0 ]; then
    if [ \"\$new_release\" -eq 1 ]; then
      /run/current-system/sw/bin/rm -rf '$REMOTE_ROOT'
    fi
    if [ \"\$had_previous\" -eq 1 ] && [ -d '$REMOTE_BACKUP' ]; then
      /run/current-system/sw/bin/mv '$REMOTE_BACKUP' '$REMOTE_ROOT'
    fi
    /run/current-system/sw/bin/rm -rf '$REMOTE_STAGE'
  fi
  exit \"\$status\"
}
trap rollback EXIT
/run/current-system/sw/bin/rm -rf '$REMOTE_BACKUP'
for file in flyers.ics phillies.ics eagles.ics; do
  /run/current-system/sw/bin/test -s \"$REMOTE_STAGE/\$file\"
  /run/current-system/sw/bin/chmod 0644 \"$REMOTE_STAGE/\$file\"
done
if [ -d '$REMOTE_ROOT' ]; then
  /run/current-system/sw/bin/mv '$REMOTE_ROOT' '$REMOTE_BACKUP'
  had_previous=1
fi
/run/current-system/sw/bin/mv '$REMOTE_STAGE' '$REMOTE_ROOT'
new_release=1
/run/current-system/sw/bin/chmod o+rx '$REMOTE_ROOT'
/run/current-system/sw/bin/rm -rf '$REMOTE_BACKUP'
trap - EXIT"
REMOTE_PREPARED=0

OLD_OUTPUT="${OUTPUT_DIR}.previous.$$"
if [ -d "$OUTPUT_DIR" ]; then
  $MV "$OUTPUT_DIR" "$OLD_OUTPUT"
fi
if ! $MV "$STAGE_ROOT" "$OUTPUT_DIR"; then
  if [ -d "$OLD_OUTPUT" ]; then
    $MV "$OLD_OUTPUT" "$OUTPUT_DIR"
  fi
  exit 1
fi
$RM -rf "$OLD_OUTPUT"

echo "Done. ICS files live at:"
echo "  https://ical.montycasa.com/sports/flyers.ics"
echo "  https://ical.montycasa.com/sports/phillies.ics"
echo "  https://ical.montycasa.com/sports/eagles.ics"
