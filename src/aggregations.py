import os
import pandas as pd
import logging

# Configure logger
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

def load_clean_data(file_path: str) -> pd.DataFrame:
    """Loads the cleaned parquet or CSV dataset."""
    if not os.path.exists(file_path):
        raise FileNotFoundError(f"Cleaned data file not found at: {file_path}")
    
    logger.info(f"Loading cleaned AQ data from: {file_path}")
    if file_path.endswith(".parquet"):
        df = pd.read_parquet(file_path)
    else:
        df = pd.read_csv(file_path)
        df["datetime"] = pd.to_datetime(df["datetime"])
        
    logger.info(f"Loaded clean dataset with {df.shape[0]} rows.")
    return df

def rank_stations_by_pollution(df: pd.DataFrame, parameter: str = "pm25") -> pd.DataFrame:
    """
    Groups by station location to calculate the mean, max, and reading count 
    for a specific pollutant, ranking them from most to least polluted.
    
    Args:
        df (pd.DataFrame): Cleaned DataFrame.
        parameter (str): Pollutant to rank (e.g. 'pm25', 'no2', 'so2').
        
    Returns:
        pd.DataFrame: Ranked stations with aggregate stats.
    """
    logger.info(f"Computing station rankings for parameter: {parameter}...")
    
    # Filter for the parameter
    param_df = df[df["parameter"] == parameter]
    if param_df.empty:
        logger.warning(f"No readings found for parameter: {parameter}")
        return pd.DataFrame()
        
    # Group by location and aggregate
    rankings = param_df.groupby("location")["value"].agg(
        avg_concentration="mean",
        max_concentration="max",
        reading_count="count"
    ).sort_values(by="avg_concentration", ascending=False)
    
    logger.info("Rankings computed successfully.")
    return rankings

def resample_telemetry_to_daily(df: pd.DataFrame) -> pd.DataFrame:
    """
    Resamples high-frequency (e.g., 15-minute or hourly) sensor telemetry 
    into daily averages for each unique station and parameter.
    
    This smooths out telemetry noise and creates a uniform timeline.
    
    Args:
        df (pd.DataFrame): Cleaned DataFrame with a 'datetime' column.
        
    Returns:
        pd.DataFrame: Daily averaged data with columns:
                      ['location', 'parameter', 'datetime', 'daily_avg_value']
    """
    logger.info("Resampling telemetry data to daily averages (Split-Apply-Combine)...")
    
    # 1. Ensure datetime is set as the index for resampling
    df_indexed = df.set_index("datetime")
    
    # 2. Group by location and parameter, then resample 'value' per Day ('D')
    # Using .mean() calculates the daily average concentration
    daily_series = df_indexed.groupby(["location", "parameter"])["value"].resample("D").mean()
    
    # 3. Convert the multi-indexed Series back into a clean flat DataFrame
    daily_df = daily_series.reset_index()
    daily_df = daily_df.rename(columns={"value": "daily_avg_value"})
    
    logger.info(f"Resampling complete. Daily shape: {daily_df.shape[0]} rows")
    return daily_df

def save_aggregations(df: pd.DataFrame, output_path: str) -> None:
    """Saves aggregated metrics cleanly to a Parquet/CSV file."""
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    
    logger.info(f"Saving aggregated dataset to: {output_path}")
    if output_path.endswith(".parquet"):
        df.to_parquet(output_path, index=False)
    else:
        df.to_csv(output_path, index=False)
    logger.info("✅ Aggregated data saved successfully!")

if __name__ == "__main__":
    BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    CLEAN_PATH = os.path.join(BASE_DIR, "data", "processed", "india_aq_clean.parquet")
    DAILY_OUT_PATH = os.path.join(BASE_DIR, "data", "processed", "india_aq_daily_avg.parquet")
    
    try:
        # Load, resample, and save
        clean_df = load_clean_data(CLEAN_PATH)
        
        # 1. Print rankings for PM2.5
        pm25_rankings = rank_stations_by_pollution(clean_df, "pm25")
        print("\n🏆 --- Top 5 Most Polluted Stations (PM2.5 Daily Avg) ---")
        print(pm25_rankings.head(5))
        print("----------------------------------------------------\n")
        
        # 2. Resample telemetry to daily averages
        daily_df = resample_telemetry_to_daily(clean_df)
        save_aggregations(daily_df, DAILY_OUT_PATH)
        
    except Exception as e:
        logger.error(f"Failed to run aggregations: {str(e)}")
