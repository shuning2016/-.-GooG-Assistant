#!/usr/bin/env bash
# install_launchagents.sh
# Copy the repo's SeaTalk LaunchAgent plists into ~/Library/LaunchAgents,
# reload them, then run seatalk_doctor.py. Re-run after any plist change so
# the installed copies never drift from the repo again.
#
# Reloading keeps the same jobs and schedules; it does not trigger a run
# (RunAtLoad is false).

set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DEST="$HOME/Library/LaunchAgents"
DOMAIN="gui/$(id -u)"

for label in com.goog-assistant.seatalk-snapshot com.goog-assistant.seatalk-summary; do
  src="$REPO/LaunchAgents/$label.plist"
  plutil -lint "$src" >/dev/null
  cp "$src" "$DEST/$label.plist"
  launchctl bootout "$DOMAIN/$label" 2>/dev/null || true
  launchctl bootstrap "$DOMAIN" "$DEST/$label.plist"
  echo "[install_launchagents] reloaded $label"
done

chmod +x "$REPO/scripts/seatalk_summary_dispatch.sh"
exec /usr/bin/python3 "$REPO/scripts/seatalk_doctor.py"
