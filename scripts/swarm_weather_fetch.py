"""
Global AQ Intelligence — Swarm Weather Fetcher
==============================================
A highly concurrent, modular script to backfill weather and AOD data across distributed nodes.
Designed to bypass Open-Meteo IP rate limits by sharding the workload across multiple VMs.

Usage:
  python scripts/swarm_weather_fetch.py --shard-id 1 --total-shards 4 --max-concurrent 10
"""
import sys
import os
import argparse
import time
import datetime
import psycopg2
from psycopg2.extras import execute_batch
import numpy as np
from concurrent.futures import ThreadPoolExecutor, as_completed
import re

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from src.config import DB_CONFIG
from src.api_fallback_manager import ApiFallbackManager

OPEN_METEO_FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
OPEN_METEO_ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"
OPEN_METEO_AQ_URL = "https://air-quality-api.open-meteo.com/v1/air-quality"


def fetch_weather_block(fallback_manager, lat, lon, start_date, end_date):
    """Fetches a block of daily weather data."""
    start_dt = datetime.datetime.strptime(start_date, "%Y-%m-%d").date()
    today = datetime.date.today()
    
    # The Open-Meteo free tier forecast API provides up to 35 days of historical data.
    # We will safely use OPEN_METEO_FORECAST_URL, but fallback to archive if it fails.
    
    params = {
        "latitude": lat,
        "longitude": lon,
        "start_date": start_date,
        "end_date": end_date,
        "daily": ["temperature_2m_mean", "wind_speed_10m_max", "precipitation_sum", "relative_humidity_2m_mean"],
        "timezone": "auto"
    }

    try:
        data = fallback_manager.request_with_fallback(
            url=OPEN_METEO_FORECAST_URL,
            params=params,
            is_openaq=False
        )
    except Exception:
        # Fallback to archive if forecast fails
        data = fallback_manager.request_with_fallback(
            url=OPEN_METEO_ARCHIVE_URL,
            params=params,
            is_openaq=False
        )

    daily = data.get("daily", {})
    if not daily or "time" not in daily:
        raise ValueError(f"Open-Meteo weather returned empty for {lat},{lon} ({start_date} to {end_date})")

    # Map time to variables
    results = {}
    for i, t in enumerate(daily["time"]):
        results[t] = {
            "om_temperature": daily.get("temperature_2m_mean", [])[i] if i < len(daily.get("temperature_2m_mean", [])) else None,
            "om_wind_speed": daily.get("wind_speed_10m_max", [])[i] if i < len(daily.get("wind_speed_10m_max", [])) else None,
            "om_precipitation": daily.get("precipitation_sum", [])[i] if i < len(daily.get("precipitation_sum", [])) else None,
            "humidity": daily.get("relative_humidity_2m_mean", [])[i] if i < len(daily.get("relative_humidity_2m_mean", [])) else None,
        }
    return results


def fetch_aod_block(fallback_manager, lat, lon, start_date, end_date):
    """Fetches a block of hourly AOD data and aggregates it to daily."""
    params = {
        "latitude": lat,
        "longitude": lon,
        "start_date": start_date,
        "end_date": end_date,
        "hourly": ["aerosol_optical_depth"],
        "timezone": "auto"
    }

    data = fallback_manager.request_with_fallback(
        url=OPEN_METEO_AQ_URL,
        params=params,
        is_openaq=False
    )

    hourly = data.get("hourly", {})
    if not hourly or "time" not in hourly:
        raise ValueError(f"Open-Meteo AOD returned empty for {lat},{lon} ({start_date} to {end_date})")

    times = hourly["time"]
    aods = hourly.get("aerosol_optical_depth", [])

    # Aggregate hourly to daily
    daily_aod = {}
    for i, t in enumerate(times):
        day = t[:10]  # Extract YYYY-MM-DD
        val = aods[i] if i < len(aods) else None
        
        if day not in daily_aod:
            daily_aod[day] = []
        if val is not None:
            daily_aod[day].append(val)

    # Compute daily means
    results = {}
    for day, vals in daily_aod.items():
        if vals:
            results[day] = {"om_aerosol_optical_depth": sum(vals) / len(vals)}
        else:
            results[day] = {"om_aerosol_optical_depth": 0.0}

    return results


def process_station(fallback_manager, station_id, lat, lon, dates):
    """Fetches and processes missing data for a single station."""
    # Find min and max date to create a single block
    sorted_dates = sorted(dates)
    start_date = sorted_dates[0].strftime("%Y-%m-%d")
    end_date = sorted_dates[-1].strftime("%Y-%m-%d")

    try:
        weather_map = fetch_weather_block(fallback_manager, lat, lon, start_date, end_date)
        aod_map = fetch_aod_block(fallback_manager, lat, lon, start_date, end_date)
    except Exception as e:
        return {"station_id": station_id, "success": False, "error": str(e), "updates": []}

    updates = []
    # Only update the specific dates that were originally missing
    for dt in dates:
        dt_str = dt.strftime("%Y-%m-%d")
        w_data = weather_map.get(dt_str, {})
        a_data = aod_map.get(dt_str, {})

        updates.append((
            w_data.get("om_temperature"),
            w_data.get("om_wind_speed"),
            w_data.get("om_precipitation"),
            a_data.get("om_aerosol_optical_depth"),
            station_id,
            dt
        ))

    return {"station_id": station_id, "success": True, "updates": updates}


def main():
    parser = argparse.ArgumentParser(description="Swarm Weather Fetcher")
    parser.add_argument("--shard-id", type=int, required=True, help="ID of this shard (1 to N)")
    parser.add_argument("--total-shards", type=int, required=True, help="Total number of shards")
    parser.add_argument("--max-concurrent", type=int, required=True, help="Max concurrent API calls (Semaphore)")
    args = parser.parse_args()

    if args.shard_id < 1 or args.shard_id > args.total_shards:
        print("❌ Invalid shard ID.")
        sys.exit(1)

    print(f"🚀 Starting Swarm Node {args.shard_id}/{args.total_shards} with {args.max_concurrent} workers...")

    conn = psycopg2.connect(**DB_CONFIG)

    # 1. Fetch all missing weather/AOD data
    with conn.cursor() as cur:
        cur.execute("""
            SELECT df.station_id, df.date, s.latitude, s.longitude
            FROM daily_features df
            JOIN stations s ON df.station_id = s.id
            WHERE df.om_temperature IS NULL OR df.om_precipitation IS NULL
            ORDER BY df.station_id, df.date
        """)
        rows = cur.fetchall()

    if not rows:
        print("✅ No missing weather data found in the database. Node resting.")
        conn.close()
        return

    # 2. Group by station
    station_data = {}
    for sid, dt, lat, lon in rows:
        if sid not in station_data:
            station_data[sid] = {"lat": lat, "lon": lon, "dates": []}
        station_data[sid]["dates"].append(dt)

    # 3. Dynamic Partitioning (Sharding)
    all_station_ids = sorted(list(station_data.keys()))
    shards = np.array_split(all_station_ids, args.total_shards)
    my_shard_ids = shards[args.shard_id - 1]

    if len(my_shard_ids) == 0:
        print(f"✅ Shard {args.shard_id} is empty (more shards than stations). Node resting.")
        conn.close()
        return

    print(f"📊 Global stations missing data: {len(all_station_ids)}")
    print(f"🎯 Assigned to Node {args.shard_id}: {len(my_shard_ids)} stations")

    # Prepare ApiFallbackManager
    raw_keys = os.getenv("OPENAQ_KEYS", "")
    raw_keys = re.sub(r'[\\r\\n]+', ',', raw_keys)
    clean_keys = [k.strip() for k in raw_keys.split(",") if k.strip()]
    fallback_manager = ApiFallbackManager(openaq_keys=clean_keys, max_retries=3, base_backoff=2.0)

    # 4. Asynchronous Concurrency with ThreadPool
    successful_updates = []
    failed_stations = 0

    with ThreadPoolExecutor(max_workers=args.max_concurrent) as executor:
        futures = []
        for sid in my_shard_ids:
            sid = int(sid)  # Cast away from numpy.int64 to prevent psycopg2 errors
            lat = station_data[sid]["lat"]
            lon = station_data[sid]["lon"]
            dates = station_data[sid]["dates"]
            futures.append(executor.submit(process_station, fallback_manager, sid, lat, lon, dates))

        for i, future in enumerate(as_completed(futures)):
            result = future.result()
            if result["success"]:
                successful_updates.extend(result["updates"])
            else:
                failed_stations += 1
                if failed_stations <= 5:
                    print(f"  ⚠️ Failed Station {result['station_id']}: {result['error']}")
            
            if (i + 1) % 50 == 0:
                print(f"  ⏳ Processed [{i+1}/{len(my_shard_ids)}] stations in shard...")

    # 5. Bulk Upserting
    if successful_updates:
        print(f"💾 Bulk updating {len(successful_updates)} missing rows in Azure DB...")
        with conn.cursor() as cur:
            execute_batch(cur, """
                UPDATE daily_features
                SET om_temperature = %s,
                    om_wind_speed = %s,
                    om_precipitation = %s,
                    om_aerosol_optical_depth = %s
                WHERE station_id = %s AND date = %s
            """, successful_updates, page_size=1000)
        conn.commit()
        print("✅ Bulk update complete!")

    if failed_stations > 0:
        print(f"⚠️ {failed_stations} stations failed. They will require retry later.")

    conn.close()
    print("🎉 Swarm Node finished successfully.")

if __name__ == "__main__":
    main()