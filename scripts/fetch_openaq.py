"""
Multi-country AQ data fetcher (AWS S3 Arch).
Generic version of fetch_openaq_india.py that accepts any country.

Supports:
    - Paginated measurement fetching via S3 Archive
    - Date range filtering
    - Checkpoint/resume (safe to restart)
    - Idempotent (UNIQUE index prevents duplicates)

Usage:
    python scripts/fetch_openaq.py --country US --days 7
    python scripts/fetch_openaq.py --country GB --days 30
"""

import os
import sys
import json
import time
import argparse
import asyncio
import aiohttp
import ssl
import certifi
import gzip
import csv
import io
import psycopg2
from psycopg2.extras import execute_values
from datetime import datetime, timedelta, timezone

# Add project root to path
import sys as _sys, os as _os
_sys.path.insert(0, _os.path.join(_os.path.dirname(__file__), ".."))
from src.config import DB_CONFIG

# Country config
COUNTRIES = {
    "IN": {"openaq_id": 9,    "name": "India"},
    "US": {"openaq_id": 155,  "name": "United States"},
    "GB": {"openaq_id": 79,   "name": "United Kingdom"},
    "AU": {"openaq_id": 177,  "name": "Australia"},
}

# Config
DATE_FROM = "2021-01-01"
CHECKPOINT_DIR = os.path.join(os.path.dirname(__file__), ".checkpoints")
CONCURRENCY = 5  # Low concurrency for Azure VM to protect memory

def get_db_connection():
    return psycopg2.connect(**DB_CONFIG)

def get_checkpoint_file(country_code):
    os.makedirs(CHECKPOINT_DIR, exist_ok=True)
    return os.path.join(CHECKPOINT_DIR, f"checkpoint_{country_code}.json")

# Note: We skip `fetch_country_stations` via API now.
# Stations MUST already be in the database from the previous API backfill.
# If a new station appears, it won't be picked up. We assume stations are static for now.
def get_station_id_map(conn, country_code):
    """Get openaq_id -> internal id mapping for a country.

    Only stations with a non-NULL openaq_id are returned. ~1400 legacy
    stations (from the old API backfill) have openaq_id IS NULL — they can
    never be fetched from S3, so including them just burns loop iterations
    and prints "Fetching station None...".
    """
    with conn.cursor() as cur:
        cur.execute(
            "SELECT id, openaq_id FROM stations "
            "WHERE country_code = %s AND openaq_id IS NOT NULL ORDER BY id",
            (country_code,)
        )
        return [{"internal_id": row[0], "openaq_id": row[1]} for row in cur.fetchall()]


# Checkpoint management
def load_checkpoint(country_code):
    path = get_checkpoint_file(country_code)
    if os.path.exists(path):
        with open(path, "r") as f:
            return json.load(f)
    return {"last_completed_openaq_id": None, "completed_count": 0}

def save_checkpoint(country_code, openaq_id, count):
    path = get_checkpoint_file(country_code)
    with open(path, "w") as f:
        json.dump({
            "last_completed_openaq_id": openaq_id,
            "completed_count": count,
            "timestamp": datetime.now().isoformat(),
        }, f, indent=2)

def clear_checkpoint(country_code):
    path = get_checkpoint_file(country_code)
    if os.path.exists(path):
        os.remove(path)

async def fetch_day_for_station(session, station, date_obj, semaphore):
    loc_id = station["openaq_id"]
    yyyy = f"{date_obj.year:04d}"
    mm = f"{date_obj.month:02d}"
    yyyymmdd = date_obj.strftime("%Y%m%d")
    
    url = f"https://openaq-data-archive.s3.amazonaws.com/records/csv.gz/locationid={loc_id}/year={yyyy}/month={mm}/location-{loc_id}-{yyyymmdd}.csv.gz"
    
    for attempt in range(3):
        try:
            async with semaphore:
                async with session.get(url) as r:
                    if r.status in (404, 403):
                        return [] 
                    if r.status != 200:
                        await asyncio.sleep(1)
                        continue
                    data = await r.read()
                    
            decompressed = gzip.decompress(data).decode('utf-8')
            reader = csv.DictReader(io.StringIO(decompressed))
            
            rows = []
            for row in reader:
                try:
                    rows.append((
                        station["internal_id"],
                        int(row["sensors_id"]),
                        row["parameter"],
                        float(row["value"]),
                        row["units"],
                        row["datetime"], 
                        row["datetime"]  
                    ))
                except (KeyError, ValueError, TypeError):
                    continue
            return rows
        except Exception:
            await asyncio.sleep(1)
            continue
    return []

async def process_station_async(session, station, date_list, semaphore, conn):
    tasks = []
    for d in date_list:
        tasks.append(fetch_day_for_station(session, station, d, semaphore))
    
    chunk_size = 30
    total_inserted = 0
    for i in range(0, len(tasks), chunk_size):
        chunk_tasks = tasks[i:i+chunk_size]
        results = await asyncio.gather(*chunk_tasks)
        
        flat_rows = []
        for r in results:
            flat_rows.extend(r)
            
        if flat_rows:
            inserted = insert_measurements(conn, flat_rows)
            total_inserted += inserted
            
    return total_inserted

def insert_measurements(conn, rows):
    if not rows:
        return 0
    sql = """
        INSERT INTO raw_measurements
            (station_id, sensor_id, parameter, value, unit, datetime_utc, datetime_local)
        VALUES %s
        ON CONFLICT (station_id, parameter, datetime_utc) DO NOTHING
    """
    # Count ACTUAL inserts, not rows attempted. ON CONFLICT DO NOTHING silently
    # drops duplicates, so returning len(rows) made re-fetching already-stored
    # days report huge fake counts (e.g. "829,121 rows inserted" while the DB
    # gained nothing) — which hid the fact that S3 had no new data at all.
    #
    # execute_values() sets cur.rowcount from only the LAST page it sends, so
    # we page manually and accumulate to stay correct for batches > page_size.
    page_size = 5000
    inserted = 0
    with conn.cursor() as cur:
        for i in range(0, len(rows), page_size):
            page = rows[i:i + page_size]
            execute_values(cur, sql, page, page_size=page_size)
            if cur.rowcount and cur.rowcount > 0:
                inserted += cur.rowcount
    conn.commit()
    return inserted


def run_fetch(country_code, days=None, date_from=None, date_to=None, resume=False):
    if country_code not in COUNTRIES:
        raise ValueError(f"Unknown country: {country_code}")

    country_name = COUNTRIES[country_code]["name"]
    now = datetime.now(timezone.utc)

    # Generate date list
    date_list = []
    if date_from and date_to:
        curr = datetime.strptime(date_from[:10], "%Y-%m-%d").date()
        end = datetime.strptime(date_to[:10], "%Y-%m-%d").date()
    elif days:
        curr = (now - timedelta(days=days)).date()
        end = now.date()
    else:
        curr = datetime.strptime(DATE_FROM[:10], "%Y-%m-%d").date()
        end = now.date()

    while curr <= end:
        date_list.append(curr)
        curr += timedelta(days=1)

    print(f"\n{'='*60}")
    print(f"  {country_name} ({country_code}) -- AQ Data Fetch (S3 ARCHIVE)")
    print(f"  Date range: {date_list[0]} to {date_list[-1]} ({len(date_list)} days)")
    print(f"{'='*60}")

    conn = get_db_connection()
    stations = get_station_id_map(conn, country_code)
    print(f"  Found {len(stations)} existing stations in database")

    checkpoint = load_checkpoint(country_code) if resume else {
        "last_completed_openaq_id": None, "completed_count": 0
    }
    skip_until = checkpoint["last_completed_openaq_id"]
    skipping = skip_until is not None
    completed = checkpoint["completed_count"]

    stations_to_process = []
    for station in stations:
        if skipping:
            if station["openaq_id"] == skip_until:
                skipping = False
            continue
        stations_to_process.append(station)

    total_rows = 0

    async def run_chunked_processing():
        nonlocal total_rows, completed
        semaphore = asyncio.Semaphore(CONCURRENCY)
        ssl_context = ssl.create_default_context(cafile=certifi.where())
        connector = aiohttp.TCPConnector(limit=CONCURRENCY, ssl=ssl_context)
        
        async with aiohttp.ClientSession(connector=connector) as session:
            for idx, station in enumerate(stations_to_process):
                if idx % 10 == 0:
                    print(f"  [{completed+1} / {len(stations)}] Fetching station {station['openaq_id']}...")
                
                inserted = await process_station_async(session, station, date_list, semaphore, conn)
                total_rows += inserted
                completed += 1
                
                if idx % 50 == 0:
                    save_checkpoint(country_code, station["openaq_id"], completed)
                    
    asyncio.run(run_chunked_processing())

    stats = {
        "country": country_code,
        "stations_found": len(stations),
        "stations_processed": completed,
        "rows_inserted": total_rows,
        "timestamp": datetime.now().isoformat(),
    }

    print(f"\n  {country_name} complete: {completed} stations, {total_rows} rows inserted")
    clear_checkpoint(country_code)
    conn.close()

    return stats


def main():
    parser = argparse.ArgumentParser(description="Multi-Country AQ Data Fetcher (S3)")
    parser.add_argument("--country", type=str, required=True,
                        choices=list(COUNTRIES.keys()),
                        help="Country code: IN, US, GB, CN, AU")
    parser.add_argument("--days", type=int, default=None,
                        help="Fetch last N days (default: full backfill)")
    parser.add_argument("--resume", action="store_true",
                        help="Resume from checkpoint")
    args = parser.parse_args()

    stats = run_fetch(args.country, args.days, resume=args.resume)

    conn = get_db_connection()
    with conn.cursor() as cur:
        cur.execute("""
            SELECT s.country_code, COUNT(DISTINCT s.id) as stations,
                   COUNT(r.id) as measurements
            FROM stations s
            LEFT JOIN raw_measurements r ON s.id = r.station_id
            GROUP BY s.country_code
            ORDER BY s.country_code
        """)
        print(f"\n  Database status:")
        for row in cur.fetchall():
            print(f"    {row[0]}: {row[1]} stations, {row[2]:,} measurements")
    conn.close()


if __name__ == "__main__":
    main()
