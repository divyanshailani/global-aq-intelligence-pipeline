#!/usr/bin/env python3
"""Verify the insert-count + S3-fallback fixes.

The bug chain being tested:
  1. insert_measurements() returned len(rows) instead of actual inserts, so
     re-fetching already-stored days reported "829,121 rows inserted" while
     the DB gained nothing.
  2. That fake non-zero count meant the zero-row S3 fallback never fired, so
     IN/GB stayed 4 days stale even though the live API had the data.
"""
import importlib.util
import os
import sys

PIPE = "/Users/divyanshailani/Desktop/pow-eda-pipeline"
sys.path.insert(0, PIPE)
sys.path.insert(0, PIPE)

import psycopg2  # noqa: E402
from src.config import DB_CONFIG  # noqa: E402

fails, passes = [], []


def check(name, cond, detail=""):
    (passes if cond else fails).append(name)
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}" + (f" — {detail}" if detail and not cond else ""))


spec = importlib.util.spec_from_file_location("fo", os.path.join(PIPE, "scripts", "pipeline", "fetch_openaq.py"))
fo = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fo)

# ── 1. insert_measurements returns TRUE inserts ────────────────────
print("\n1. insert_measurements() counts actual inserts")
conn = psycopg2.connect(**DB_CONFIG)
cur = conn.cursor()
# Shadow the real table with a temp one of the same name/shape so the
# function under test writes somewhere disposable.
cur.execute("""
    CREATE TEMP TABLE raw_measurements (
        id serial, station_id int, sensor_id int, parameter text,
        value double precision, unit text,
        datetime_utc timestamptz, datetime_local timestamptz,
        UNIQUE (station_id, parameter, datetime_utc)
    )
""")
conn.commit()

def mkrows(n, start=0):
    return [(1, 100 + i, "pm25", 5.0, "µg/m³",
             f"2026-01-01 {(start + i) % 24:02d}:00:00+00",
             f"2026-01-01 {(start + i) % 24:02d}:00:00+00")
            for i in range(n)]

# Distinct datetimes are required for uniqueness. Walk real calendar time from
# a base date so we never emit an invalid day like "2026-01-209".
import datetime as _dt  # noqa: E402

_BASE = _dt.datetime(2020, 1, 1, tzinfo=_dt.timezone.utc)

def mkrows_days(n, day_offset=0):
    rows = []
    for i in range(n):
        ts = _BASE + _dt.timedelta(hours=day_offset * 24 + i)
        s = ts.strftime("%Y-%m-%d %H:%M:%S+00")
        rows.append((1, 100, "pm25", 5.0, "µg/m³", s, s))
    return rows

check("empty input returns 0", fo.insert_measurements(conn, []) == 0)

batch = mkrows_days(10)
n1 = fo.insert_measurements(conn, batch)
check("10 fresh rows -> returns 10", n1 == 10, f"got {n1}")

n2 = fo.insert_measurements(conn, batch)
check("same 10 rows again -> returns 0 (not 10)", n2 == 0,
      f"got {n2} — this is the bug that faked 829k inserts")

mixed = batch[:5] + mkrows_days(5, day_offset=200)
n3 = fo.insert_measurements(conn, mixed)
check("5 dupes + 5 new -> returns 5", n3 == 5, f"got {n3}")

# Critical: batch larger than page_size must not undercount.
big = mkrows_days(12000, day_offset=5000)
n4 = fo.insert_measurements(conn, big)
check("12000 fresh rows across pages -> returns 12000 (not last page only)",
      n4 == 12000, f"got {n4} — execute_values rowcount is per-page")

cur.execute("SELECT COUNT(*) FROM raw_measurements")
total = cur.fetchone()[0]
check("returned counts match real table total", total == 10 + 5 + 12000,
      f"table has {total}, expected {10+5+12000}")

conn.rollback()
conn.close()

# ── 2. Fallback fires when S3 yields nothing ───────────────────────
print("\n2. Zero-row S3 result triggers live-API fallback")
src = open(os.path.join(PIPE, "scripts", "pipeline", "run_daily_collector.py")).read()
check("zero-row S3 is treated as a miss", "s3_rows == 0" in src)
check("raises to enter fallback branch", "S3 archive returned no rows" in src)
check("fallback reports its own row count", "api_rows" in src)
# Scope to run_incremental: a file-wide find() hits an earlier `try:` in
# get_gap_days and gives a false negative.
_inc = src[src.find("def run_incremental"):src.find("def run_backfill")]
check("fetch_days defined before try (no NameError in except)",
      _inc.find("fetch_days = 7") < _inc.find("try:")
      and _inc.find("fetch_days = 7") != -1)

# The old code only fell back on exception; confirm that's gone.
check("no longer relies solely on exception to fall back",
      "S3 FETCH MISS" in src and "S3 FETCH FAILED" not in src)

print(f"\n{'='*52}")
print(f"  {len(passes)} passed, {len(fails)} failed")
if fails:
    print("  FAILED: " + ", ".join(fails))
print(f"{'='*52}")
sys.exit(1 if fails else 0)
