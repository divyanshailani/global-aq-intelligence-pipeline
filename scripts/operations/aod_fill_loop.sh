#!/bin/bash
# Local AOD fill loop for the Open-Meteo air-quality host.
#
# Why local: that host's hourly budget is a handful of REQUESTS per IP
# (~4-6 measured on 2026-09-28), independent of whether each call covers 1 day
# or 14, and it does not refill within minutes. GitHub runner IPs are shared
# and effectively exhausted for it, while a home IP answers a 200-location
# call in ~1.6s - so the fill belongs on the owner's machine, not in CI.
#
# Each pass fetches only rows that are still NULL (get_missing), stops on the
# script's 3-consecutive-failure breaker once the hour's budget is gone, then
# waits 20 minutes and tries again. Progress is therefore monotonic and the
# loop exits by itself when the range has no AOD holes left.
#
# Usage:  scripts/operations/aod_fill_loop.sh [start] [end]
#         defaults to the 2026-08-09..09-24 starved window
set -u

cd "$(dirname "$0")/../.." || exit 1
START=${1:-2026-08-09}
END=${2:-2026-09-24}
LOG=logs/aod_fill.log
PY=${PY:-python3}

remaining() {
  "$PY" - "$START" "$END" <<'PY'
import sys

import psycopg2

from src.config import DB_CONFIG

start, end = sys.argv[1], sys.argv[2]
conn = psycopg2.connect(**DB_CONFIG)
cur = conn.cursor()
cur.execute(
    "SELECT count(*) FROM daily_features WHERE date BETWEEN %s AND %s "
    "AND om_aerosol_optical_depth IS NULL",
    (start, end),
)
print(cur.fetchone()[0])
PY
}

while true; do
  echo "=== pass $(date -u +%FT%TZ) ==="
  "$PY" -u scripts/operations/backfill_weather_batch.py \
      --start "$START" --end "$END" --skip-weather 2>&1 | tee -a "$LOG"
  n=$(remaining)
  echo "remaining AOD nulls in $START..$END: $n"
  if [ "$n" = "0" ]; then
    echo LOOP_DONE_GAP_CLOSED
    break
  fi
  echo "--- waiting 20 min for the hourly quota to refill ---"
  sleep 1200
done
