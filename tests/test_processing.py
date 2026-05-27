import pytest
import pandas as pd
import numpy as np
from src.process_aq import clean_data, validate_data

@pytest.fixture
def sample_raw_data() -> pd.DataFrame:
    """Fixture supplying a mock raw air quality telemetry dataset with clean and dirty data."""
    return pd.DataFrame({
        "location_id": [17, 17, 17, 50, 50],
        "location": ["R K Puram", "R K Puram", "R K Puram", "Punjabi Bagh", "Punjabi Bagh"],
        "sensor_id": [1223, 1223, 1223, 5678, 5678],
        "parameter": ["pm25", "pm25", "pm25", "no2", "no2"],
        "units": ["µg/m³", "µg/m³", "µg/m³", "ppb", "ppb"],
        "value": [45.2, 45.2, -99.0, 12.5, 30.0],  # Includes negative and duplicate values
        "datetime": [
            "2025-02-18T20:00:00Z", 
            "2025-02-18T20:00:00Z",  # Duplicate row (index 1 is exact duplicate of index 0)
            "2025-02-18T21:00:00Z",  # Negative concentration (dirty data)
            "2025-02-18T20:00:00Z", 
            "2025-02-18T21:00:00Z"
        ],
        "datetime_local": [
            "2025-02-19T01:30:00+05:30",
            "2025-02-19T01:30:00+05:30",
            "2025-02-19T02:30:00+05:30",
            "2025-02-19T01:30:00+05:30",
            "2025-02-19T02:30:00+05:30"
        ]
    })

def test_clean_data_removes_duplicates_and_negatives(sample_raw_data):
    """Asserts that duplicate rows and negative concentrations are successfully filtered."""
    cleaned = clean_data(sample_raw_data)
    
    # 1. Assert size decrease:
    # Original has 5 rows:
    # - Row 1 is a duplicate of Row 0.
    # - Row 2 has a value of -99.0.
    # Cleaned should have 3 rows remaining.
    assert cleaned.shape[0] == 3
    
    # 2. Assert no negative values exist:
    assert (cleaned["value"] >= 0).all()
    
    # 3. Assert datetime columns are parsed into datetime64 types
    assert pd.api.types.is_datetime64_any_dtype(cleaned["datetime"])
    assert pd.api.types.is_datetime64_any_dtype(cleaned["datetime_local"])

def test_clean_data_immutability(sample_raw_data):
    """Asserts that clean_data does not mutate the original input DataFrame (first-principles immutability)."""
    original_copy = sample_raw_data.copy()
    _ = clean_data(sample_raw_data)
    
    # Verify the original DataFrame was untouched
    pd.testing.assert_frame_equal(sample_raw_data, original_copy)

def test_validation_passes_valid_data(sample_raw_data):
    """Asserts that validate_data successfully passes a clean dataset."""
    cleaned = clean_data(sample_raw_data)
    assert validate_data(cleaned) is True

def test_validation_fails_on_negatives(sample_raw_data):
    """Asserts that validate_data raises an AssertionError if negative values are present."""
    # Let's bypass clean_data and directly pass a dataset with a negative value
    dirty_data = sample_raw_data.copy()
    
    # Ensure datetimes are parsed so it doesn't fail on type check
    dirty_data["datetime"] = pd.to_datetime(dirty_data["datetime"])
    
    with pytest.raises(AssertionError, match="Found negative values"):
        validate_data(dirty_data)
