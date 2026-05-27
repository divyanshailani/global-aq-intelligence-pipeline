"""
OpenAQ India Air Quality Data Fetcher
=====================================
Downloads raw AQ measurements from Indian CPCB monitoring stations
via the OpenAQ v3 API.

Usage:
    export OPENAQ_API_KEY="your_key_here"
    python fetch_openaq_india.py

Output:
    data/raw/india_aq_raw.csv
"""

import requests
import csv
import os
import time

API_BASE = "https://api.openaq.org/v3"
INDIA_COUNTRY_ID = 9

# Diverse stations across India
STATIONS = [
    (17, "R K Puram Delhi"),
    (50, "Punjabi Bagh Delhi"),
    (235, "Anand Vihar Delhi"),
    (15, "IGI Airport Delhi"),
    (301, "Vikas Sadan Gurugram"),
    (407, "Zoo Park Hyderabad"),
    (354, "Lalbagh Mumbai"),
    (378, "Alandur Chennai"),
    (286, "Collectorate Gaya"),
    (103, "Income Tax Delhi"),
]


def get_headers():
    key = os.environ.get("OPENAQ_API_KEY")
    if not key:
        raise ValueError("Set OPENAQ_API_KEY environment variable first!")
    return {"X-API-Key": key}


def fetch_measurements(station_id, station_name, headers):
    """Fetch all sensor measurements for a given station."""
    rows = []

    # Get sensors for this location
    r = requests.get(
        f"{API_BASE}/locations/{station_id}/sensors",
        headers=headers,
    )
    if r.status_code != 200:
        print(f"  ❌ Failed to get sensors: {r.status_code}")
        return rows

    sensors = r.json().get("results", [])

    for sensor in sensors:
        sensor_id = sensor["id"]
        param = sensor["parameter"]["name"]
        units = sensor["parameter"]["units"]

        # Get measurements
        mr = requests.get(
            f"{API_BASE}/sensors/{sensor_id}/measurements",
            headers=headers,
            params={"limit": 1000},
        )
        if mr.status_code != 200:
            continue

        measurements = mr.json().get("results", [])
        for m in measurements:
            rows.append({
                "location_id": station_id,
                "location": station_name,
                "sensor_id": sensor_id,
                "parameter": param,
                "units": units,
                "value": m["value"],
                "datetime": m["period"]["datetimeFrom"]["utc"],
                "datetime_local": m["period"]["datetimeFrom"].get("local", ""),
            })

        print(f"  {param}: {len(measurements)} readings")
        time.sleep(0.2)  # rate limit courtesy

    return rows


def main():
    headers = get_headers()
    data_dir = os.path.join(os.path.dirname(__file__), "..", "data", "raw")
    os.makedirs(data_dir, exist_ok=True)

    all_rows = []
    for loc_id, name in STATIONS:
        print(f"📡 Downloading: {name} (ID={loc_id})...")
        rows = fetch_measurements(loc_id, name, headers)
        all_rows.extend(rows)

    print(f"\n📊 Total rows collected: {len(all_rows)}")

    # Save as CSV
    outfile = os.path.join(data_dir, "india_aq_raw.csv")
    if all_rows:
        with open(outfile, "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=all_rows[0].keys())
            writer.writeheader()
            writer.writerows(all_rows)
        size_mb = os.path.getsize(outfile) / (1024 * 1024)
        print(f"✅ Saved to: {outfile} ({size_mb:.1f} MB)")


if __name__ == "__main__":
    main()
