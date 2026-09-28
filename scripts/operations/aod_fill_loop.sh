#!/bin/bash
# Local AOD fill loop for the Open-Meteo air-quality host.
#
# Quota model (measured 2026-09-28): the free tier allows roughly 10,000
# weighted units per IP per DAY, where a request costs locations x days for
# hourly variables. A 200-location x 14-day call therefore costs ~2,800 units -
# so one IP can make only ~3-4 such calls before the host refuses *everything*,
# including 1-location requests (verified: after a handful of batches, even a
# 1-location x 1-day call returned 429 in 0.7s). Silence does not help; 20
# minutes quiet and 48 minutes quiet both produced zero successes, because the
# budget resets daily, not per hour. The daily pipeline never trips this only
# because it calls per station (~1 unit each).
#
# Consequences for this loop: spend the day's units in a short burst, then stay
# quiet for hours instead of retrying into a wall. The script's breaker stops
# after a single rejection (--aod-max-failures 1) so probing costs one attempt.
#
# GitHub runner IPs are separately exhausted for this host, which is why the
# fill belongs on the owner's machine (~1.6s per call).
#
# Usage:  scripts/operations/aod_fill_loop.sh [start] [end]
#         defaults to the 2026-08-09..09-24 starved window
set -u

cd "$(dirname "$0")/../.." || exit 1
START=${1:-2026-08-09}
END=${2:-2026-09-24}
LOG=logs/aod_fill.log
PY=${PY:-python3}
REST=${REST:-21600}     # 6h: roughly one burst per quarter of the daily budget

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
    echo "--- progress $before -> $after: units are available, spending them ---"
    sleep 45
  else
    echo "--- throttled: resting $((REST / 3600))h until the daily budget resets ---"
    sleep "$REST"
  fi
done
