#!/bin/bash
# Local AOD fill loop for the Open-Meteo air-quality host.
#
# Why local: that host throttles by IP and its budget only refills while the IP
# is quiet. Measurements on 2026-09-28: a burst of four 200-location x 7-day
# requests succeeded back-to-back after ~40 minutes of silence (5,600 rows),
# while a loop that probed every 20 minutes with a 3-failure breaker filled
# nothing for over an hour - repeated futile attempts kept the IP starved. So
# the loop probes with a SINGLE request (--aod-max-failures 1) and adapts:
# keep going while requests land, then rest and let the budget rebuild.
#
# GitHub runner IPs are shared and effectively exhausted for this host, which
# is why the fill belongs on the owner's machine (~1.6s per call).
#
# Usage:  scripts/operations/aod_fill_loop.sh [start] [end]
#         defaults to the 2026-08-09..09-24 starved window
set -u

cd "$(dirname "$0")/../.." || exit 1
START=${1:-2026-08-09}
END=${2:-2026-09-24}
LOG=logs/aod_fill.log
PY=${PY:-python3}

# Station-days still missing AOD, counting distinct (station_id, date): the
# table is grained per (station_id, date, parameter), so counting rows would
# overstate the gap and keep the loop alive after the real holes were gone.
remaining() {
  "$PY" - "$START" "$END" <<'PY'
import sys

import psycopg2

from src.config import DB_CONFIG

start, end = sys.argv[1], sys.argv[2]
conn = psycopg2.connect(**DB_CONFIG)
cur = conn.cursor()
cur.execute(
    "SELECT count(DISTINCT (station_id, date)) FROM daily_features "
    "WHERE om_aerosol_optical_depth IS NULL AND date BETWEEN %s AND %s",
    (start, end),
)
print(cur.fetchone()[0])
PY
}

while true; do
  before=$(remaining)
  echo "=== pass $(date -u +%FT%TZ) — holes: $before ==="
  "$PY" -u scripts/operations/backfill_weather_batch.py \
      --start "$START" --end "$END" --skip-weather --aod-max-failures 1 2>&1 | tee -a "$LOG"
  after=$(remaining)
  if [ "$after" = "0" ]; then
    echo LOOP_DONE_GAP_CLOSED
    break
  fi
  if [ "$after" -lt "$before" ]; then
    echo "--- progress $before -> $after: the host is allowing requests, continuing ---"
    sleep 90
  else
    echo "--- throttled: resting 20 min so the IP's budget can refill ---"
    sleep 1200
  fi
done
