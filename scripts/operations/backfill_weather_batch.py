#!/usr/bin/env python3
"""
One-off batch backfill of missing Open-Meteo weather + AOD for a date range,
plus a forced recompute of the derived rolling features (rolling_3day_precip,
aod_volatility_index) that were previously computed over NULL inputs.

Background:
  Since ~2026-07-24 the daily pipeline hit Open-Meteo's free-tier limits
  (per-row fetching, 10K calls/day IP cap) and left om_* weather columns
  NULL in daily_features. XGBoost treats NULL as NaN, so models silently
  lost their weather/AOD features.

Why this fetches per-coordinate RANGES and not per date:
  Open-Meteo weights a call by locations x variables x time span
  (open-meteo.com/en/terms). One date at a time costs ~V/10 weighted calls
  per coordinate-day; a >=7 day range costs ~V/70. For the 2026-08-09..09-24
  gap the per-date strategy needs ~29k weighted calls (three days of the free
  tier) while ranges need ~4k (under half of one day) — which is exactly why
  the per-date runs kept dying on 429s partway through the gap.

Run from the repo root:
  python3 scripts/operations/backfill_weather_batch.py --start 2026-08-09 --end 2026-09-24
  python3 scripts/operations/backfill_weather_batch.py --start 2026-08-09 --end 2026-09-24 --dry-run
"""

import argparse
import os
import random
import re
import sys
import time
from collections import defaultdict
from datetime import date, datetime, timedelta

import psycopg2
from psycopg2.extras import execute_batch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from src.config import DB_CONFIG  # loads .env automatically
from src.api_fallback_manager import ApiFallbackManager
from src.features import build_advanced_weather_features
from scripts.pipeline.fetch_daily_weather import fetch_weather_batch_range
from scripts.pipeline.fetch_daily_aod import fetch_aod_batch_range

CHUNK = 200            # coordinates per multi-location call
AOD_WINDOW_DAYS = 14   # The air-quality host's hourly cap counts *requests*, not
                       # weighted location-days: measured 2026-09-28, one IP got
                       # ~4 successful 200-location calls per hour whether each
                       # call covered 1 day or 7 (a weighted model predicts 25+
                       # successes/hour for the 1-day shape - never observed, while
                       # 4 x 7-day windows wrote 5,600 rows in one burst). Width is
                       # therefore free upside, so use the span the fetcher is
                       # designed for: ~1 MB responses, far larger than 1 day.
SLEEP = 3.0            # seconds between calls; free tier also limits calls/minute
FAILED_SLEEP = 30.0    # cool-down after a failed call so retries cannot stampede
                       # into a self-inflicted minutely-limit breach
MAX_CONSECUTIVE_FAILURES = 3   # stop a phase once the hourly budget is clearly
                               # gone instead of grinding 65s retries for hours

UPDATE_SQL = """
    UPDATE daily_features
    SET om_temperature = COALESCE(%s, om_temperature),
        om_wind_speed = COALESCE(%s, om_wind_speed),
        om_precipitation = COALESCE(%s, om_precipitation),
        om_aerosol_optical_depth = COALESCE(%s, om_aerosol_optical_depth)
    WHERE station_id = %s AND date = %s
      AND (om_temperature IS NULL
           OR om_precipitation IS NULL
           OR om_aerosol_optical_depth IS NULL)
"""


def get_missing(conn, start: str, end: str):
    """Missing station-days, indexed by coordinate.

    Returns (dates_by_coord, sids_by_coord, n_station_days). Weather is fetched
    per coordinate, so one fetch serves every station sitting on it.
    """
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
        """, (start, end))
        rows = cur.fetchall()

    dates_by_coord = defaultdict(set)
    sids_by_coord = defaultdict(set)
    for dt, sid, lat, lon in rows:
        dates_by_coord[(lat, lon)].add(dt)
        sids_by_coord[(lat, lon)].add(sid)
    return dates_by_coord, sids_by_coord, len(rows)


def fetch_with_retry(fetcher, *args, tries: int = 3, label: str = ""):
    """Run a range fetch, waiting out Open-Meteo's minutely window on 429s.

    Open-Meteo answers 429 with "Minutely API request limit exceeded" (~600
    calls/min). Retrying inside that window only deepens the hole: short nested
    retries keep the window saturated and every later call fails too, which is
    how an earlier version of this script stalled for 40 minutes. So the ladder
    makes exactly one request per attempt and waits >=65s, which clears the
    window regardless of what a concurrent caller is doing.
    """
    last = None
    for attempt in range(tries):
        try:
            return fetcher(*args)
        except Exception as e:
            last = e
            msg = str(e)
            if "429" in msg or "Overloaded" in msg or "Timeout" in msg:
                delay = min(150, 65 * (attempt + 1)) + random.uniform(0, 5)
                print(f"    retry {attempt + 1}/{tries} for {label} "
                      f"in {delay:.0f}s ({msg[:90]})")
                time.sleep(delay)
            else:
                raise
    raise last


def endpoint_regimes(days):
    """Split sorted dates into (start, end) spans per Open-Meteo endpoint.

    The archive API serves dates more than 7 days old, the forecast API the
    rest; the fetchers pick the endpoint from the span's start date, so the two
    regimes must never share a call.
    """
    cutoff = date.today() - timedelta(days=7)
    spans = []
    for regime, group in (("archive", [d for d in days if d < cutoff]),
                          ("forecast", [d for d in days if d >= cutoff])):
        if group:
            spans.append((group[0], group[-1], regime))
    return spans


def chunk_dates(days, size):
    """Consecutive spans of at most `size` dates (an AOD call cannot span years)."""
    spans = []
    for i in range(0, len(days), size):
        win = days[i:i + size]
        spans.append((win[0], win[-1]))
    return spans


def write_updates(conn, updates, label):
    """execute_batch with one reconnect retry. Returns (conn, rows_written)."""
    if not updates:
        return conn, 0
    for attempt in (1, 2):
        try:
            with conn.cursor() as cur:
                execute_batch(cur, UPDATE_SQL, updates, page_size=500)
            conn.commit()
            return conn, len(updates)
        except (psycopg2.OperationalError, psycopg2.InterfaceError) as e:
            print(f"  ⚠️ {label} DB write failed (attempt {attempt}/2): {e}")
            time.sleep(5)
            try:
                conn.rollback()
            except psycopg2.Error:
                pass
            conn = psycopg2.connect(**DB_CONFIG)
            print("  DB reconnected.")
    return conn, 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start", required=True, help="YYYY-MM-DD, inclusive")
    parser.add_argument("--end", required=True, help="YYYY-MM-DD, inclusive")
    parser.add_argument("--dry-run", action="store_true",
                        help="Fetch + count, but write nothing to the DB")
    parser.add_argument("--skip-aod", action="store_true",
                        help="Fill weather only. The air-quality host's budget is "
                             "tiny and only rebuilds while the IP is quiet, so give "
                             "AOD its own run instead of sharing one.")
    parser.add_argument("--skip-weather", action="store_true",
                        help="Fill AOD only (the two hosts throttle independently).")
    parser.add_argument("--shard", metavar="I/N",
                        help="Process only coordinates whose index mod N is I. The "
                             "air-quality host allows a fixed number of weighted "
                             "requests per IP per day, so running N shards on N "
                             "machines (or N CI runners) multiplies the fill rate "
                             "instead of queueing behind one allowance.")
    parser.add_argument("--aod-max-failures", type=int, default=MAX_CONSECUTIVE_FAILURES,
                        help="Stop the AOD phase after this many consecutive rejected "
                             "requests (default %(default)s). Use 1 when probing a host "
                             f"that penalises attempts: the air-quality budget refills "
                             f"only while the IP is quiet, so a burst of futile retries "
                             f"is what keeps it starved.")
    args = parser.parse_args()

    raw_keys = re.sub(r"[\r\n]+", ",", os.getenv("OPENAQ_KEYS", ""))
    clean_keys = [k.strip() for k in raw_keys.split(",") if k.strip()]
    # max_retries=1: the manager must not run its own fast retry loop. Nesting it
    # under fetch_with_retry turns one 429 into a burst that saturates
    # Open-Meteo's minutely window and fails every later call.
    fallback = ApiFallbackManager(openaq_keys=clean_keys, max_retries=1, base_backoff=2.0)

    conn = psycopg2.connect(**DB_CONFIG)
    print(f"DB: {DB_CONFIG['host']}/{DB_CONFIG['dbname']}")

    dates_by_coord, sids_by_coord, n_rows = get_missing(conn, args.start, args.end)
    coords = sorted(dates_by_coord)
    if args.shard:
        # i/N: keep only the coordinates this worker owns. The air-quality host
        # meters a daily allowance per IP, so fanning one gap across several
        # machines (each with its own IP) multiplies throughput; the split is by
        # coordinate so two workers never fetch the same rows.
        i, n = (int(x) for x in args.shard.split("/"))
        coords = [c for k, c in enumerate(coords) if k % n == i]
        n_rows = sum(len(dates_by_coord[c]) for c in coords)
        print(f"Shard {i}/{n}: {n_rows:,} station-days on {len(coords):,} coordinates")
    if not coords:
        print(f"Nothing to backfill in {args.start} .. {args.end}.")
        conn.close()
        return
    n_chunks = (len(coords) + CHUNK - 1) // CHUNK
    print(f"Missing station-days: {n_rows:,} on {len(coords):,} coordinates "
          f"({args.start} .. {args.end}) → {n_chunks} chunks of {CHUNK}")

    t0 = time.time()
    written = 0
    failed_calls = 0
    calls = 0
    consecutive_failures = 0
    aod_exhausted = False

    for ci, c0 in enumerate(range(0, len(coords), CHUNK), 1):
        chunk = coords[c0:c0 + CHUNK]
        days = sorted(set().union(*(dates_by_coord[c] for c in chunk)))
        lats_str = ",".join(str(c[0]) for c in chunk)
        lons_str = ",".join(str(c[1]) for c in chunk)

        weather = [{} for _ in chunk]   # per coordinate: date -> feature dict
        aod = [{} for _ in chunk]       # per coordinate: date -> mean AOD

        for lo, hi, regime in [] if args.skip_weather else endpoint_regimes(days):
            try:
                calls += 1
                result = fetch_with_retry(
                    fetch_weather_batch_range, fallback, lats_str, lons_str,
                    lo.isoformat(), hi.isoformat(),
                    label=f"weather {regime} {lo}..{hi} chunk {ci}")
                for fetched, target in zip(result, weather):
                    target.update(fetched)
            except Exception as e:
                failed_calls += 1
                print(f"  ⚠️ weather {regime} {lo}..{hi} chunk {ci}/{n_chunks} "
                      f"({len(chunk)} locations) failed: {str(e)[:120]}")
                time.sleep(FAILED_SLEEP)
            time.sleep(SLEEP + random.uniform(0, 1.0))

        for lo, hi in chunk_dates(days, AOD_WINDOW_DAYS) if not args.skip_aod else []:
            if aod_exhausted:
                break
            try:
                calls += 1
                # tries=1: each attempt that reaches the host costs quota, and a
                # failed one is usually a rate-limited hour, not a transient —
                # waiting 65s inside it only delays the remaining windows. The
                # next dispatch resumes whatever this pass could not fill.
                result = fetch_with_retry(
                    fetch_aod_batch_range, fallback, lats_str, lons_str,
                    lo.isoformat(), hi.isoformat(), tries=1,
                    label=f"AOD {lo}..{hi} chunk {ci}")
                for fetched, target in zip(result, aod):
                    target.update(fetched)
                consecutive_failures = 0
            except Exception as e:
                failed_calls += 1
                consecutive_failures += 1
                print(f"  ⚠️ AOD {lo}..{hi} chunk {ci}/{n_chunks} "
                      f"({len(chunk)} locations) failed, AOD left NULL: {str(e)[:120]}")
                if consecutive_failures >= args.aod_max_failures:
                    print(f"  ⛔ {consecutive_failures} consecutive AOD rejections — "
                          f"this IP is throttled. Stopping the AOD phase; "
                          f"everything written is committed. Run again later with the "
                          f"same range to resume where this stopped.")
                    aod_exhausted = True
                time.sleep(FAILED_SLEEP)
            time.sleep(SLEEP + random.uniform(0, 1.0))

        updates = []
        for i, coord in enumerate(chunk):
            for day in dates_by_coord[coord]:
                iso = day.isoformat()
                w = weather[i].get(iso)
                a = aod[i].get(iso)
                if w is None and a is None:
                    continue
                for sid in sids_by_coord[coord]:
                    updates.append((
                        w["om_temperature"] if w else None,
                        w["om_wind_speed"] if w else None,
                        w["om_precipitation"] if w else None,
                        a,
                        int(sid),
                        day,
                    ))

        label = f"chunk {ci}/{n_chunks}"
        if args.dry_run:
            print(f"  [{ci}/{n_chunks}] {len(chunk)} coords, {len(days)} dates, "
                  f"{len(updates):,} rows fetched ({time.time() - t0:.0f}s elapsed)")
            written += len(updates)
        else:
            conn, n = write_updates(conn, updates, label)
            written += n
            print(f"  [{ci}/{n_chunks}] {len(chunk)} coords, {len(days)} dates, "
                  f"{n:,} rows written this chunk, {written:,} cumulative "
                  f"({time.time() - t0:.0f}s elapsed)")

    print(f"\nFetched {written:,} station-day rows from {calls} API calls, "
          f"{failed_calls} failed calls ({time.time() - t0:.0f}s).")

    if args.dry_run:
        print("Dry-run samples (temp, wind, precip, aod, station, date):")
        for u in updates[:3]:
            print(f"  {u}")
        print("DRY RUN — no DB writes performed.")
        conn.close()
        return

    # ── Forced recompute of features derived from the columns just filled ──
    print("\nRecomputing rolling_3day_precip / aod_volatility_index ...")
    t1 = time.time()
    updated = build_advanced_weather_features(conn, force=True, since=args.start)
    print(f"  {updated:,} rows recomputed ({time.time() - t1:.0f}s)")

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
            print(f"  {dt}: {nt:,} null temp / {na:,} null AOD")

    conn.close()
    print(f"\nDone in {time.time() - t0:.0f}s.")


if __name__ == "__main__":
    main()
