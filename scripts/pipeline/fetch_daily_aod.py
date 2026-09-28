import datetime
from src.api_fallback_manager import ApiFallbackManager

# Open-Meteo Air Quality API
OPEN_METEO_AQ_URL = "https://air-quality-api.open-meteo.com/v1/air-quality"

def fetch_aod_for_date(fallback_manager: ApiFallbackManager, lat: float, lon: float, target_date: str):
    """
    Fetches the daily Aerosol Optical Depth (AOD) for a specific latitude, longitude, and date.
    Returns a dictionary of the extracted features or raises RuntimeError if it fails.
    """
    
    params = {
        "latitude": lat,
        "longitude": lon,
        "start_date": target_date,
        "end_date": target_date,
        "hourly": ["aerosol_optical_depth"],
        "timezone": "auto"
    }

    # This will throw a RuntimeError if it exhausts all backoff retries
    data = fallback_manager.request_with_fallback(
        url=OPEN_METEO_AQ_URL,
        params=params,
        is_openaq=False
    )
    
    hourly = data.get("hourly", {})
    aod_array = hourly.get("aerosol_optical_depth", [])
    
    if not aod_array:
        raise ValueError(f"Open-Meteo AOD returned empty array for {lat},{lon} on {target_date}")
    
    # Filter out None values
    valid_aod = [v for v in aod_array if v is not None]
    
    if not valid_aod:
        # If all hours are missing AOD, return 0.0 or None (let the caller decide, we'll return 0.0 for safety)
        mean_aod = 0.0
    else:
        mean_aod = sum(valid_aod) / len(valid_aod)
        
    return {
        "om_aerosol_optical_depth": mean_aod
    }


def fetch_aod_batch_for_date(fallback_manager: ApiFallbackManager, lats_str: str, lons_str: str, target_date: str):
    """
    Fetches the daily mean AOD for MANY coordinates (comma-separated lats/lons) and one date,
    in a SINGLE multi-location Open-Meteo air-quality call.
    Returns a list of {"om_aerosol_optical_depth": mean} dicts, in request order.
    Raises RuntimeError/ValueError if the call fails or the response is incomplete.
    """
    params = {
        "latitude": lats_str,
        "longitude": lons_str,
        "start_date": target_date,
        "end_date": target_date,
        "hourly": ["aerosol_optical_depth"],
        "timezone": "auto"
    }

    data = fallback_manager.request_with_fallback(
        url=OPEN_METEO_AQ_URL,
        params=params,
        is_openaq=False
    )

    if not isinstance(data, list) or len(data) != len(lats_str.split(",")):
        raise ValueError(f"Open-Meteo AOD batch returned {type(data).__name__} with "
                         f"{len(data) if isinstance(data, list) else 'n/a'} entries for {len(lats_str.split(','))} locations on {target_date}")

    out = []
    for entry in data:
        hourly = entry.get("hourly", {})
        aod_array = hourly.get("aerosol_optical_depth", [])

        if not aod_array:
            raise ValueError(f"Open-Meteo AOD batch returned empty array for location {entry.get('latitude')},{entry.get('longitude')} on {target_date}")

        valid_aod = [v for v in aod_array if v is not None]
        mean_aod = sum(valid_aod) / len(valid_aod) if valid_aod else 0.0
        out.append({"om_aerosol_optical_depth": mean_aod})
    return out


def fetch_aod_batch_range(fallback_manager: ApiFallbackManager, lats_str: str, lons_str: str,
                          start_date: str, end_date: str):
    """
    Fetches daily-mean AOD for MANY coordinates over a multi-day RANGE in one call.

    AOD is hourly, so responses grow with the span: the caller is expected to pass
    short windows (~14 days) to keep each response around 1 MB.
    Daily means use the same convention as fetch_aod_batch_for_date — mean of the
    non-NULL hours, 0.0 for a day with no valid hour.
    Returns a list (request order) of {date_iso: mean_aod} dicts.
    """
    n_locations = len(lats_str.split(","))
    params = {
        "latitude": lats_str,
        "longitude": lons_str,
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

    if not isinstance(data, list) or len(data) != n_locations:
        raise ValueError(f"Open-Meteo AOD range batch returned {type(data).__name__} with "
                         f"{len(data) if isinstance(data, list) else 'n/a'} entries for "
                         f"{n_locations} locations ({start_date}..{end_date})")

    out = []
    for entry in data:
        hourly = entry.get("hourly", {})
        times = hourly.get("time", [])
        if not times:
            raise ValueError(f"Open-Meteo AOD range batch returned empty hourly array for "
                             f"{entry.get('latitude')},{entry.get('longitude')} ({start_date}..{end_date})")
        aods = hourly.get("aerosol_optical_depth", [])
        buckets = {}
        for i, t in enumerate(times):
            day = t[:10]
            val = aods[i] if i < len(aods) else None
            buckets.setdefault(day, [])
            if val is not None:
                buckets[day].append(val)
        out.append({day: (sum(vals) / len(vals) if vals else 0.0)
                    for day, vals in buckets.items()})
    return out
