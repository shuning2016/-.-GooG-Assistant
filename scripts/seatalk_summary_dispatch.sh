#!/usr/bin/env bash
# seatalk_summary_dispatch.sh
# Called by the LaunchAgent at 10:00, 12:00, 15:00, and 19:00 SGT.
# Maps the current hour to the appropriate --hours window and invokes
# seatalk_summary.py.
#
# Hour → window mapping:
#   10 → 3 h  (07:00–10:00)
#   12 → 2 h  (10:00–12:00)
#   15 → 3 h  (12:00–15:00)
#   19 → 4 h  (15:00–19:00)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SUMMARY_PY="$SCRIPT_DIR/seatalk_summary.py"

# Determine current hour in local time (SGT when Mac is set to Asia/Singapore)
HOUR=$(date +%H)
# Strip leading zero so bash arithmetic works
HOUR=$((10#$HOUR))

case $HOUR in
  10) HOURS=3 ;;
  12) HOURS=2 ;;
  15) HOURS=3 ;;
  19) HOURS=4 ;;
  *)
    # Unexpected hour — use a safe default of 2 h
    HOURS=2
    echo "[seatalk_summary_dispatch] Unexpected hour=$HOUR, defaulting to --hours $HOURS" >&2
    ;;
esac

echo "[seatalk_summary_dispatch] hour=$HOUR → python3 $SUMMARY_PY --hours $HOURS"
exec /usr/bin/python3 "$SUMMARY_PY" --hours "$HOURS"
