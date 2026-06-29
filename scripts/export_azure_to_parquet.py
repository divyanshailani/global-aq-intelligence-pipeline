"""
Global AQ Intelligence — Azure → Parquet Export
=================================================
Pulls the entire daily_features table (joined with stations for lat/lon)
from Azure PostgreSQL and saves it as a compressed Parquet file locally.

Usage:
    pip install psycopg2-binary pandas pyarrow
    python3 scripts/export_azure_to_parquet.py
"""

import os
import time
import pandas as pd
import psycopg2

OUTPUT_DIR = os.path.join(os.path.dirname(__file__), "..", "data")
OUTPUT_FILE = os.path.join(OUTPUT_DIR, "daily_features_full.parquet")

def require_env(name: str) -> str:
    """Read required database settings without shipping credentials in git."""
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(f"{name} is required. Put it in .env or your secret manager.")
    return value


DB_CONFIG = {
    "host": require_env("POSTGRES_HOST"),
    "user": require_env("POSTGRES_USER"),
    "password": require_env("POSTGRES_PASSWORD"),
    "dbname": require_env("POSTGRES_DB"),
    "port": int(os.getenv("POSTGRES_PORT", "5432")),
    "sslmode": os.getenv("POSTGRES_SSLMODE", "require"),
}

QUERY = """
    SELECT
        df.date,
        df.station_id,
        df.parameter,
        df.value,
        df.month,
        df.day_of_week,
        df.is_weekend,
        df.day_of_year,
        df.lag_1, df.lag_2, df.lag_3, df.lag_7,
        df.lag_14, df.lag_21, df.lag_30,
        df.roll_3_mean, df.roll_7_mean, df.roll_3_std,
        df.roll_14_mean, df.roll_30_mean, df.roll_14_std,
        df.pm25_delta_1, df.pm25_delta_7,
        df.om_temperature,
        df.om_wind_speed,
        df.om_precipitation,
        df.om_aerosol_optical_depth,
        df.rolling_3day_precip,
        df.aod_volatility_index,
        df.country_code,
        s.latitude,
        s.longitude
    FROM daily_features df
    JOIN stations s ON df.station_id = s.id
    ORDER BY df.station_id, df.date
"""


def main():
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    print("🔌 Connecting to Azure PostgreSQL...")
    conn = psycopg2.connect(**DB_CONFIG)

    print("📥 Pulling daily_features + stations (this may take 1-2 min)...")
    t0 = time.time()
    df = pd.read_sql(QUERY, conn)
    pull_secs = time.time() - t0
    conn.close()

    print(f"   ✅ Fetched {len(df):,} rows × {len(df.columns)} cols in {pull_secs:.1f}s")

    # Downcast types to save space
    for col in ["month", "day_of_week", "day_of_year"]:
        if col in df.columns:
            df[col] = df[col].astype("Int16")

    df["is_weekend"] = df["is_weekend"].astype("boolean")
    df["station_id"] = df["station_id"].astype("int32")

    print(f"💾 Writing Parquet → {os.path.abspath(OUTPUT_FILE)}")
    t0 = time.time()
    df.to_parquet(OUTPUT_FILE, engine="pyarrow", compression="snappy", index=False)
    write_secs = time.time() - t0

    file_size_mb = os.path.getsize(OUTPUT_FILE) / (1024 * 1024)
    print(f"   ✅ Written in {write_secs:.1f}s | Size: {file_size_mb:.1f} MB")

    # Quick sanity check
    print("\n📊 Sanity Check:")
    print(f"   Rows:       {len(df):,}")
    print(f"   Columns:    {len(df.columns)}")
    print(f"   Date range: {df['date'].min()} → {df['date'].max()}")
    print(f"   Stations:   {df['station_id'].nunique():,}")
    print(f"   Null value: {df['value'].isna().sum()}")
    print(f"   Null temp:  {df['om_temperature'].isna().sum()}")
    print(f"   Null AOD:   {df['om_aerosol_optical_depth'].isna().sum():,}")
    print(f"\n🎉 Export complete! File ready at: {os.path.abspath(OUTPUT_FILE)}")


if __name__ == "__main__":
    main()
