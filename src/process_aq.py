import os
import pandas as pd
import logging

# Configure elegant logger
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

def load_raw_data(file_path: str) -> pd.DataFrame:
    """
    Safely loads the raw CSV air quality telemetry data.
    
    Args:
        file_path (str): Path to the raw CSV file.
        
    Returns:
        pd.DataFrame: Loaded raw DataFrame.
    """
    if not os.path.exists(file_path):
        raise FileNotFoundError(f"Raw data file not found at: {file_path}")
    
    logger.info(f"Loading raw AQ telemetry from: {file_path}")
    df = pd.read_csv(file_path)
    logger.info(f"Successfully loaded. Shape: {df.shape[0]} rows, {df.shape[1]} columns")
    return df

def clean_data(df: pd.DataFrame) -> pd.DataFrame:
    """
    Applies pure, immutable cleaning transformations to the raw air quality DataFrame.
    
    Cleaning Operations:
    1. Parse datetime fields to pandas datetime64 format.
    2. Drop exact row duplicates.
    3. Filter out invalid negative pollutant concentrations.
    
    Args:
        df (pd.DataFrame): Raw DataFrame.
        
    Returns:
        pd.DataFrame: Cleaned DataFrame (copied to prevent state mutation).
    """
    logger.info("Starting cleaning operations (preserving immutability)...")
    
    # 1. Create a copy to prevent SettingWithCopy / state mutation
    clean_df = df.copy()
    
    # 2. Parse Datetime columns
    logger.info("Parsing datetime and datetime_local...")
    clean_df["datetime"] = pd.to_datetime(clean_df["datetime"], errors="coerce")
    clean_df["datetime_local"] = pd.to_datetime(clean_df["datetime_local"], errors="coerce")
    
    # 3. Drop Duplicate Rows
    initial_rows = clean_df.shape[0]
    clean_df = clean_df.drop_duplicates()
    dropped_dupes = initial_rows - clean_df.shape[0]
    if dropped_dupes > 0:
        logger.info(f"Dropped {dropped_dupes} exact duplicate rows.")
    
    # 4. Filter Negative Concentrations
    # Pollutant values can never be negative (sensor noise/calibration errors)
    initial_rows = clean_df.shape[0]
    clean_df = clean_df[clean_df["value"] >= 0].copy()
    dropped_negatives = initial_rows - clean_df.shape[0]
    if dropped_negatives > 0:
        logger.warning(f"Filtered out {dropped_negatives} rows with negative values (< 0).")
        
    logger.info(f"Cleaning complete. Cleaned shape: {clean_df.shape[0]} rows")
    return clean_df

def validate_data(df: pd.DataFrame) -> bool:
    """
    Performs critical schema and sanity checks on the cleaned DataFrame.
    
    Checks:
    - Schema: Assert all required columns exist.
    - Zero Nulls: Assert no missing values in key identifier or measurement columns.
    - Boundaries: Assert all measurements are non-negative.
    
    Args:
        df (pd.DataFrame): Cleaned DataFrame to check.
        
    Returns:
        bool: True if validation passes, raises AssertionError otherwise.
    """
    logger.info("Running first-principles data validation checks...")
    
    # Check 1: Required Schema columns
    required_cols = {"location_id", "location", "sensor_id", "parameter", "units", "value", "datetime"}
    assert required_cols.issubset(df.columns), f"Missing required columns! Found: {df.columns}"
    
    # Check 2: No null values in critical columns
    critical_cols = ["location_id", "parameter", "value", "datetime"]
    for col in critical_cols:
        null_count = df[col].isna().sum()
        assert null_count == 0, f"Validation Failed: Found {null_count} null values in column '{col}'!"
        
    # Check 3: Measurement bounds
    min_val = df["value"].min()
    assert min_val >= 0, f"Validation Failed: Found negative values! Min value is {min_val}"
    
    logger.info("✅ All data validation checks passed successfully!")
    return True

def save_processed_data(df: pd.DataFrame, output_path: str) -> None:
    """
    Safely exports the processed DataFrame to the specified path.
    Supported formats: CSV and Parquet.
    
    Args:
        df (pd.DataFrame): Cleaned DataFrame.
        output_path (str): Target output file path.
    """
    # Ensure parent directories exist
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    
    logger.info(f"Saving processed data to: {output_path}")
    if output_path.endswith(".parquet"):
        df.to_parquet(output_path, index=False)
    else:
        df.to_csv(output_path, index=False)
        
    size_mb = os.path.getsize(output_path) / (1024 * 1024)
    logger.info(f"✅ Data saved successfully! File size: {size_mb:.2f} MB")

def run_pipeline(raw_path: str, processed_path: str) -> None:
    """Runs the full ingestion, cleaning, validation, and export pipeline."""
    try:
        raw_df = load_raw_data(raw_path)
        cleaned_df = clean_data(raw_df)
        validate_data(cleaned_df)
        save_processed_data(cleaned_df, processed_path)
        logger.info("🎉 Ingestion & Processing Pipeline executed flawlessly!")
    except Exception as e:
        logger.error(f"❌ Pipeline failed: {str(e)}")
        raise e

if __name__ == "__main__":
    # Setup default paths relative to this script
    BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    RAW_PATH = os.path.join(BASE_DIR, "data", "raw", "india_aq_raw.csv")
    PROCESSED_PATH = os.path.join(BASE_DIR, "data", "processed", "india_aq_clean.parquet")
    
    run_pipeline(RAW_PATH, PROCESSED_PATH)
