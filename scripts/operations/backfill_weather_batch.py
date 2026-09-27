#!/usr/bin/env python3
"""
One-off batch backfill of missing Open-Meteo weather + AOD for a date range,
plus a SQL repair of the frozen rolling features (rolling_3day_precip,
aod_volatility_index) that were computed over the NULL values.

Background:
  Since ~2026-07-24 the daily pipeline hit Open-Meteo's free-tier limits
  (per-row fetching, 10K calls/day IP cap) and left om_* weather columns
  NULL in daily_features. XGBoost treats NULL as NaN, so models silently
  lost their weather/AOD features. This script:

    1. Finds distinct (date, station_id, lat, lon) rows with NULL weather.
    2. Fetches weather + AOD via multi-location batch Open-Meteo calls
       (~250 coordinates per call; archive API for dates >7 days old,
       forecast API for recent dates).
    3. Overwrite-updates only the rows that still have NULL om_*.
    4. Re-runs the pipeline's Phase 4 rolling SQL for all rows since
       --repair-cutoff so rolling features are computed over real data.

Defaults cover the observed gap: 2026-07-25 .. 2026-09-24.
Run from the repo root:
  python3 scripts/operations/backfill_weather_batch.py --start 2026-07-25 --end 2026-09-24
  python3 scripts/operations/backfill_weather_batch.py --start 2026-09-22 --end 2026-09-23 --dry-run
"""

import argparse
import os
import re
import random
import sys
import time
from collections import defaultdict
from datetime import date, datetime, timedelta

import psycopg2
from psycopg2.extras import execute_batch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from src.config import DB_CONFIG  # loads .env automatically
from src.api_fallback_manager import ApiFallbackManager
from scripts.pipeline.fetch_daily_weather import fetch_weather_batch_for_date
from scripts.pipeline.fetch_daily_aod import fetch_aod_batch_for_date

CHUNK = 250  # coordinates per multi-location call (URL-length limit ~300)
SLEEP = 3.0   # seconds between chunks — Open-Meteo free tier rate-limits hard
MAX_CONSECUTIVE_FAILURES = 5  # circuit breaker: stop the date if the tier is saturated

UPDATE_SQL = """
    UPDATE daily_features
    SET om_temperature = %s,
        om_wind_speed = %s,
        om_precipitation = %s,
        om_aerosol_optical_depth = %s
    WHERE station_id = %s AND date = %s
      AND (om_temperature IS NULL
           OR om_precipitation IS NULL
           OR om_aerosol_optical_depth IS NULL)
"""


def get_missing(conn, start: str, end: str):
    """Distinct station-days with NULL weather in [start, end], grouped by date."""
    with conn.cursor() as cur:
        cur.execute("""
            SELECT DISTINCT df.date, df.station_id, s.latitude, s.longitude
            FROM daily_features df
            JOIN stations s ON df.station_id = s.id
            WHERE (df.om_temperature IS NULL
               OR df.om_precipitation IS NULL
               OR df.om_aerosol_optical_depth IS NULL)
              AND s.latitude IS NOT NULL
              AND s.longitude IS NOT NULL
              AND df.date BETWEEN %s::date AND %s::date
            ORDER BY df.date, df.station_id
        """, (start, end))
        rows = cur.fetchall()

    by_date = defaultdict(list)
    for dt, sid, lat, lon in rows:
        by_date[dt].append((sid, lat, lon))
    return by_date


def fetch_with_retry(fetcher, *args, tries: int = 4, label: str = ""):
    """Run a batch fetch with exponential backoff on 429s/overloaded errors."""
    last = None
    for attempt in range(tries):
        try:
            return fetcher(*args)
        except Exception as e:
            last = e
            msg = str(e)
            if "429" in msg or "Overloaded" in msg or "Timeout" in msg:
                delay = min(180, 15 * (2 ** attempt)) + random.uniform(0, 5)
                print(f"    retry {attempt + 1}/{tries} for {label} "
                      f"in {delay:.0f}s ({msg[:90]})")
                time.sleep(delay)
            else:
                raise
    raise last


def repair_rolling(conn, cutoff: str):
    """Re-run the pipeline's Phase 4 rolling SQL for rows still frozen on
    NULL inputs (or frozen pre-backfill). Same windows/semantics as
    src/features.py:build_advanced_weather_features, scoped by date.
    """
    sql = """
        WITH rolling_data AS (
            SELECT station_id, date, parameter,
                   SUM(om_precipitation) OVER (
                       PARTITION BY station_id, parameter
                       ORDER BY date
                       ROWS BETWEEN 3 PRECEDING AND 1 PRECEDING
                   ) AS roll_3_precip,
                   STDDEV(om_aerosol_optical_depth) OVER (
                       PARTITION BY station_id, parameter
                       ORDER BY date
                       ROWS BETWEEN 7 PRECEDING AND 1 PRECEDING
                   ) AS aod_vol
            FROM daily_features
            WHERE date >= %s::date
        )
        UPDATE daily_features df
        SET rolling_3day_precip = COALESCE(rd.roll_3_precip, 0),
            aod_volatility_index = COALESCE(rd.aod_vol, 0)
        FROM rolling_data rd
        WHERE df.station_id = rd.station_id
          AND df.date = rd.date
          AND df.parameter = rd.parameter
          AND (df.rolling_3day_precip IS NULL
               OR df.aod_volatility_index IS NULL)
    """
    with conn.cursor() as cur:
        cur.execute(sql, (cutoff,))
        print(f"  🔄 Rolling repair ({cutoff} onward): {cur.rowcount} rows updated")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start", required=True, help="YYYY-MM-DD, inclusive")
    parser.add_argument("--end", required=True, help="YYYY-MM-DD, inclusive")
    parser.add_argument("--repair-cutoff", default=None,
                        help="Recompute rolling features for rows >= this date "
                             "(default: start - 7 days)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Fetch + count, but write nothing to the DB")
    args = parser.parse_args()

    repair_cutoff = args.repair_cutoff or (
        datetime.strptime(args.start, "%Y-%m-%d").date() - timedelta(days=7)
    ).isoformat()

    raw_keys = re.sub(r"[\r\n]+", ",", os.getenv("OPENAQ_KEYS", ""))
    clean_keys = [k.strip() for k in raw_keys.split(",") if k.strip()]
    fallback = ApiFallbackManager(openaq_keys=clean_keys, max_retries=3, base_backoff=2.0)

    conn = psycopg2.connect(**DB_CONFIG)
    print(f"DB: {DB_CONFIG['host']}/{DB_CONFIG['dbname']}")

    by_date = get_missing(conn, args.start, args.end)
    total = sum(len(v) for v in by_date.values())
    n_dates = len(by_date)
    print(f"Missing station-days: {total:,} across {n_dates} days "
          f"({args.start} .. {args.end})")
    if total == 0:
        print("Nothing to backfill.")
        conn.close()
        return

    updates = []
    failed_chunks = 0
    written = 0
    db_reconnects = 0
    wall_dates = 0
    t0 = time.time()

    for i, dt in enumerate(sorted(by_date), 1):
        target = dt.isoformat()
        coords = sorted({(lat, lon) for _, lat, lon in by_date[dt]})
        coord_to_sids = defaultdict(list)
        for sid, lat, lon in by_date[dt]:
            coord_to_sids[(lat, lon)].append(sid)

        date_updates = []
        consecutive_failures = 0
        date_failures = 0

        for c in range(0, len(coords), CHUNK):
            chunk = coords[c:c + CHUNK]
            lats_str = ",".join(str(lat) for lat, _ in chunk)
            lons_str = ",".join(str(lon) for _, lon in chunk)
            try:
                w_list = fetch_with_retry(
                    fetch_weather_batch_for_date, fallback, lats_str, lons_str, target,
                    label=f"{target} weather chunk {c // CHUNK + 1}")
            except Exception as e:
                failed_chunks += 1
                date_failures += 1
                consecutive_failures += 1
                print(f"  ⚠️ {target} weather chunk {c // CHUNK + 1} "
                      f"({len(chunk)} locations) failed: {e}")
                if consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
                    print(f"  ⛔ {target}: {consecutive_failures} consecutive "
                          f"failed chunks — cooling down 120s (free tier saturated)")
                    time.sleep(120)
                    consecutive_failures = 0
                time.sleep(SLEEP + random.uniform(0, 1.0))
                continue
            try:
                aod_list = fetch_with_retry(
                    fetch_aod_batch_for_date, fallback, lats_str, lons_str, target,
                    tries=2, label=f"{target} AOD chunk {c // CHUNK + 1}")
            except Exception as e:
                failed_chunks += 1
                print(f"  ⚠️ {target} AOD chunk {c // CHUNK + 1} "
                      f"({len(chunk)} locations) failed, AOD left NULL: {str(e)[:90]}")
                aod_list = [None] * len(chunk)
            consecutive_failures = 0
            for (lat, lon), w, a in zip(chunk, w_list, aod_list):
                for sid in coord_to_sids[(lat, lon)]:
                    date_updates.append((
                        float(w["om_temperature"]),
                        float(w["om_wind_speed"]),
                        float(w["om_precipitation"]),
                        float(a["om_aerosol_optical_depth"]) if a else None,
                        int(sid),
                        dt,
                    ))

            time.sleep(SLEEP + random.uniform(0, 1.0))

        updates.extend(date_updates)

        if not args.dry_run and date_updates:
            write_ok = False
            for attempt in (1, 2):
                try:
                    with conn.cursor() as cur:
                        execute_batch(cur, UPDATE_SQL, date_updates, page_size=1000)
                    conn.commit()
                    write_ok = True
                    break
                except psycopg2.OperationalError as e:
                    print(f"  ⚠️ {target} DB write failed (attempt {attempt}/2): {e}")
                    time.sleep(5)
                    try:
                        conn.rollback()
                    except psycopg2.Error:
                        pass
                    conn = psycopg2.connect(**DB_CONFIG)
                    db_reconnects += 1
                    print("  DB reconnected.")
            if write_ok:
                written += len(date_updates)
                print(f"  [{i}/{n_dates}] {target}: {len(coords)} coords, "
                      f"{len(date_updates):,} rows written this date, "
                      f"{written:,} cumulative ({time.time() - t0:.0f}s elapsed)")
            else:
                print(f"  ⚠️ {target}: {len(date_updates):,} rows fetched but NOT "
                      f"written (DB write failed twice)")
        else:
            print(f"  [{i}/{n_dates}] {target}: {len(coords)} coords, "
                  f"{len(date_updates):,} fetched ({time.time() - t0:.0f}s elapsed)")

        if (not args.dry_run and not date_updates and date_failures >= 2):
            wall_dates += 1
            if wall_dates >= 3:
                print(f"\n⛔ QUOTA WALL at {target}: 3 consecutive dates "
                      f"fully failed — free tier exhausted for now. Stopping "
                      f"early; re-run the same command later to resume "
                      f"(everything written so far is committed).")
                break
        else:
            wall_dates = 0

    print(f"\nFetched {len(updates):,} station-day weather rows, "
          f"{failed_chunks} failed chunks, {db_reconnects} DB reconnects.")

    if args.dry_run:
        # Spot-check a few fetched values
        sample = [u for u in updates if u[0] is not None][:3]
        print("Dry-run samples (temp, wind, precip, aod, station, date):")
        for u in sample:
            print(f"  {u}")
        missing_temps = sum(1 for u in updates if u[0] is None)
        print(f"Rows with NULL temperature: {missing_temps}")
        print("DRY RUN — no DB writes performed.")
        conn.close()
        return

    # (Writes already happened per-date above; resume-safe.)

    repair_rolling(conn, repair_cutoff)
    conn.commit()

    # ── Verification ──
    with conn.cursor() as cur:
        cur.execute("""
            SELECT date,
                   COUNT(*) FILTER (WHERE om_temperature IS NULL) AS null_temp,
                   COUNT(*) FILTER (WHERE om_aerosol_optical_depth IS NULL) AS null_aod
            FROM daily_features
            WHERE date BETWEEN %s::date AND %s::date
            GROUP BY date
            ORDER BY date
        """, (args.start, args.end))
        print("\nPost-backfill NULLs per date:")
        for dt, nt, na in cur.fetchall():
            print(f"  {dt.date()}: {nt:,} null temp / {na:,} null AOD")

    conn.close()
    print(f"\nDone in {time.time() - t0:.0f}s.")


if __name__ == "__main__":
    main()
