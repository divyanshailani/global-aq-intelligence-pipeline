"""
Multi-Country Daily Data Collector
====================================
Orchestrator that runs incremental data collection for all configured countries.

Fetches last 7 days of OpenAQ data for each country (incremental updates).
Designed to run daily via launchd/cron.

Usage:
    python scripts/run_daily_collector.py              # all countries, last 7 days
    python scripts/run_daily_collector.py --days 3     # all countries, last 3 days
    python scripts/run_daily_collector.py --country US # single country
"""

import os
import sys
import json
import argparse
from datetime import datetime

# Add project root to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from scripts.fetch_openaq import run_fetch, COUNTRIES

LOG_DIR = os.path.join(os.path.dirname(__file__), "..", "logs")
LOG_FILE = os.path.join(LOG_DIR, "collection_log.json")


def load_log():
    """Load existing collection log."""
    if os.path.exists(LOG_FILE):
        with open(LOG_FILE, "r") as f:
            return json.load(f)
    return {"runs": []}


def save_log(log):
    """Save collection log."""
    os.makedirs(LOG_DIR, exist_ok=True)
    with open(LOG_FILE, "w") as f:
        json.dump(log, f, indent=2, default=str)


def main():
    parser = argparse.ArgumentParser(description="Daily AQ Data Collector")
    parser.add_argument("--days", type=int, default=7,
                        help="Fetch last N days (default: 7)")
    parser.add_argument("--country", type=str, default=None,
                        choices=list(COUNTRIES.keys()),
                        help="Single country (default: all)")
    args = parser.parse_args()

    countries = [args.country] if args.country else list(COUNTRIES.keys())

    print(f"\n{'='*60}")
    print(f"  Daily AQ Collector")
    print(f"  Countries: {', '.join(countries)}")
    print(f"  Days: {args.days}")
    print(f"  Time: {datetime.now().isoformat()}")
    print(f"{'='*60}")

    log = load_log()
    run_entry = {
        "timestamp": datetime.now().isoformat(),
        "days": args.days,
        "countries": {},
    }

    total_rows = 0
    for country_code in countries:
        try:
            stats = run_fetch(country_code, days=args.days, resume=False)
            run_entry["countries"][country_code] = {
                "status": "success",
                "stations": stats["stations_found"],
                "rows_inserted": stats["rows_inserted"],
            }
            total_rows += stats["rows_inserted"]
        except Exception as e:
            print(f"\n  {country_code} FAILED: {e}")
            run_entry["countries"][country_code] = {
                "status": "failed",
                "error": str(e),
            }

    run_entry["total_rows"] = total_rows
    log["runs"].append(run_entry)

    # Keep only last 30 runs
    log["runs"] = log["runs"][-30:]

    save_log(log)

    print(f"\n{'='*60}")
    print(f"  Collection complete: {total_rows} total rows")
    print(f"  Log saved to: {LOG_FILE}")
    print(f"{'='*60}")


if __name__ == "__main__":
    main()
