"""
Bulk Backfill (Local) — OpenAQ S3 Architecture
==============================================
Runs on local Mac Mini. 
Massive concurrency S3 scraper to backfill missing June 2026-present data.
Bypasses the OpenAQ API rate limits completely.
"""

import os
import sys
import gzip
import csv
import io
import time
import asyncio
import aiohttp
import ssl
import certifi
import psycopg2
from psycopg2.extras import execute_values
from datetime import datetime, timedelta

# Add project root to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
from src.config import DB_CONFIG

# Configuration
CONCURRENCY = 100
START_DATE = datetime(2026, 6, 16).date()
END_DATE = datetime.utcnow().date()
TARGET_COUNTRIES = ["US", "GB", "AU", "IN"] 

def get_db_connection():
    return psycopg2.connect(**DB_CONFIG)

def get_stations(conn, countries):
    """Get openaq_id -> internal id mapping for target countries."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT id, openaq_id, country_code FROM stations WHERE country_code = ANY(%s) ORDER BY id",
            (countries,)
        )
        return [{"internal_id": row[0], "openaq_id": row[1], "country": row[2]} for row in cur.fetchall()]

def insert_measurements_bulk(conn, rows):
    if not rows:
        return 0
    sql = """
        INSERT INTO raw_measurements
            (station_id, sensor_id, parameter, value, unit, datetime_utc, datetime_local)
        VALUES %s
        ON CONFLICT (station_id, parameter, datetime_utc) DO NOTHING
    """
    with conn.cursor() as cur:
        execute_values(cur, sql, rows, page_size=5000)
    conn.commit()
    return len(rows)

async def fetch_day_for_station(session, station, date_obj, semaphore):
    # S3 URL Format:
    # https://openaq-data-archive.s3.amazonaws.com/records/csv.gz/locationid=1/year=2006/month=11/location-1-20061113.csv.gz
    loc_id = station["openaq_id"]
    yyyy = f"{date_obj.year:04d}"
    mm = f"{date_obj.month:02d}"
    yyyymmdd = date_obj.strftime("%Y%m%d")
    
    url = f"https://openaq-data-archive.s3.amazonaws.com/records/csv.gz/locationid={loc_id}/year={yyyy}/month={mm}/location-{loc_id}-{yyyymmdd}.csv.gz"
    
    for attempt in range(3):
        try:
            async with semaphore:
                async with session.get(url) as r:
                    if r.status == 404 or r.status == 403:
                        return [] # file does not exist
                    if r.status != 200:
                        await asyncio.sleep(1)
                        continue
                    
                    data = await r.read()
                    
            decompressed = gzip.decompress(data).decode('utf-8')
            reader = csv.DictReader(io.StringIO(decompressed))
            
            rows = []
            for row in reader:
                try:
                    # S3 CSV Format: location_id,sensors_id,location,datetime,lat,lon,parameter,units,value
                    rows.append((
                        station["internal_id"],
                        int(row["sensors_id"]),
                        row["parameter"],
                        float(row["value"]),
                        row["units"],
                        row["datetime"], # UTC datetime
                        row["datetime"]  # local datetime approx
                    ))
                except (KeyError, ValueError, TypeError):
                    continue
            return rows
        except Exception as e:
            await asyncio.sleep(1)
            continue
    return []

async def process_station_backfill(session, station, date_list, semaphore, conn):
    print(f"  [{station['country']}] Station {station['openaq_id']} - Fetching {len(date_list)} days...")
    
    tasks = []
    for d in date_list:
        tasks.append(fetch_day_for_station(session, station, d, semaphore))
    
    # Process in chunks to avoid memory spikes
    chunk_size = 50
    total_inserted = 0
    for i in range(0, len(tasks), chunk_size):
        chunk_tasks = tasks[i:i+chunk_size]
        results = await asyncio.gather(*chunk_tasks)
        
        flat_rows = []
        for r in results:
            flat_rows.extend(r)
            
        if flat_rows:
            inserted = insert_measurements_bulk(conn, flat_rows)
            total_inserted += inserted
            
    print(f"    -> Inserted {total_inserted} rows for station {station['openaq_id']}")
    return total_inserted

async def run_bulk_backfill():
    conn = get_db_connection()
    stations = get_stations(conn, TARGET_COUNTRIES)
    print(f"Found {len(stations)} stations to backfill.")
    
    # Generate date list
    date_list = []
    curr = START_DATE
    while curr <= END_DATE:
        date_list.append(curr)
        curr += timedelta(days=1)
        
    print(f"Total days to scan per station: {len(date_list)}")
    
    semaphore = asyncio.Semaphore(CONCURRENCY)
    ssl_context = ssl.create_default_context(cafile=certifi.where())
    connector = aiohttp.TCPConnector(limit=CONCURRENCY, ssl=ssl_context)
    
    total_db_inserts = 0
    start_time = time.time()
    
    async with aiohttp.ClientSession(connector=connector) as session:
        for idx, station in enumerate(stations):
            print(f"\nProgress: {idx+1}/{len(stations)} stations")
            inserted = await process_station_backfill(session, station, date_list, semaphore, conn)
            total_db_inserts += inserted
            
    elapsed = time.time() - start_time
    print(f"\nBackfill Complete! Inserted {total_db_inserts:,} rows in {elapsed/60:.1f} minutes.")
    conn.close()

if __name__ == "__main__":
    asyncio.run(run_bulk_backfill())
