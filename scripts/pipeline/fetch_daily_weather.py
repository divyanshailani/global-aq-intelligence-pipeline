import datetime
from src.api_fallback_manager import ApiFallbackManager

OPEN_METEO_FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
OPEN_METEO_ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"

def fetch_weather_for_date(fallback_manager: ApiFallbackManager, lat: float, lon: float, target_date: str):
    """
    Fetches the daily weather for a specific latitude, longitude, and date.
    Returns a dictionary of the extracted features or raises RuntimeError if it fails.
    """
    target_dt = datetime.datetime.strptime(target_date, "%Y-%m-%d").date()
    today = datetime.date.today()
    
    # If the date is older than 90 days, we MUST use the archive API.
    # To be safe, we use the archive API for anything older than 7 days, 
    # since the forecast API is best for recent/future data.
    if (today - target_dt).days > 7:
        url = OPEN_METEO_ARCHIVE_URL
    else:
        url = OPEN_METEO_FORECAST_URL
    
    params = {
        "latitude": lat,
        "longitude": lon,
        "start_date": target_date,
        "end_date": target_date,
        "daily": ["temperature_2m_mean", "wind_speed_10m_max", "precipitation_sum", "relative_humidity_2m_mean"],
        "timezone": "auto"
    }

    # This will throw a RuntimeError if it exhausts all backoff retries
    data = fallback_manager.request_with_fallback(
        url=url,
        params=params,
        is_openaq=False
    )
    
    daily = data.get("daily", {})
    if not daily or "time" not in daily or len(daily["time"]) == 0:
        raise ValueError(f"Open-Meteo returned empty daily array for {lat},{lon} on {target_date}")
    
    return {
        "om_temperature": daily.get("temperature_2m_mean", [None])[0],
        "om_wind_speed": daily.get("wind_speed_10m_max", [None])[0],
        "om_precipitation": daily.get("precipitation_sum", [None])[0],
        "humidity": daily.get("relative_humidity_2m_mean", [None])[0]
    }


def fetch_weather_batch_for_date(fallback_manager: ApiFallbackManager, lats_str: str, lons_str: str, target_date: str):
    """
    Fetches the daily weather for MANY coordinates (comma-separated lats/lons) and one date,
    in a SINGLE multi-location Open-Meteo call.
    Returns a list of extracted-feature dicts, in request order.
    Raises RuntimeError/ValueError if the call fails or the response is incomplete.
    """
    target_dt = datetime.datetime.strptime(target_date, "%Y-%m-%d").date()
    today = datetime.date.today()

    if (today - target_dt).days > 7:
        url = OPEN_METEO_ARCHIVE_URL
    else:
        url = OPEN_METEO_FORECAST_URL

    params = {
        "latitude": lats_str,
        "longitude": lons_str,
        "start_date": target_date,
        "end_date": target_date,
        "daily": ["temperature_2m_mean", "wind_speed_10m_max", "precipitation_sum", "relative_humidity_2m_mean"],
        "timezone": "auto"
    }

    data = fallback_manager.request_with_fallback(
        url=url,
        params=params,
        is_openaq=False
    )

    # Multi-location responses come back as a list, in request order.
    if not isinstance(data, list) or len(data) != len(lats_str.split(",")):
        raise ValueError(f"Open-Meteo batch returned {type(data).__name__} with "
                         f"{len(data) if isinstance(data, list) else 'n/a'} entries for {len(lats_str.split(','))} locations on {target_date}")

    out = []
    for entry in data:
        daily = entry.get("daily", {})
        if not daily or "time" not in daily or len(daily["time"]) == 0:
            raise ValueError(f"Open-Meteo batch returned empty daily array for location {entry.get('latitude')},{entry.get('longitude')} on {target_date}")
        out.append({
            "om_temperature": daily.get("temperature_2m_mean", [None])[0],
            "om_wind_speed": daily.get("wind_speed_10m_max", [None])[0],
            "om_precipitation": daily.get("precipitation_sum", [None])[0],
            "humidity": daily.get("relative_humidity_2m_mean", [None])[0]
        })
    return out


def fetch_weather_batch_range(fallback_manager: ApiFallbackManager, lats_str: str, lons_str: str,
                              start_date: str, end_date: str):
    """
    Fetches daily weather for MANY coordinates over a multi-day RANGE in one call.

    Open-Meteo weights a call by locations x variables x time span, so one range
    call per coordinate costs roughly 1/7th of fetching the same days one date at
    a time (see open-meteo.com/en/terms). This is what lets a large backfill fit
    inside the free tier's 10k calls/day.

    Only the three stored om_* variables are requested: relative_humidity_2m_mean
    was fetched by the per-date variants but no pipeline or model code reads it,
    and each extra variable raises the call weight.

    Returns a list (request order) of {date_iso: {feature: value}} dicts.
    The endpoint follows the same 7-day rule as the per-date fetchers, so a single
    call must not mix dates older than 7 days with recent ones.
    """
    start_dt = datetime.datetime.strptime(start_date, "%Y-%m-%d").date()
    url = (OPEN_METEO_ARCHIVE_URL if (datetime.date.today() - start_dt).days > 7
           else OPEN_METEO_FORECAST_URL)

    n_locations = len(lats_str.split(","))
    params = {
        "latitude": lats_str,
        "longitude": lons_str,
        "start_date": start_date,
        "end_date": end_date,
        "daily": ["temperature_2m_mean", "wind_speed_10m_max", "precipitation_sum"],
        "timezone": "auto"
    }

    data = fallback_manager.request_with_fallback(
        url=url,
        params=params,
        is_openaq=False
    )

    if not isinstance(data, list) or len(data) != n_locations:
        raise ValueError(f"Open-Meteo range batch returned {type(data).__name__} with "
                         f"{len(data) if isinstance(data, list) else 'n/a'} entries for "
                         f"{n_locations} locations ({start_date}..{end_date})")

    out = []
    for entry in data:
        daily = entry.get("daily", {})
        times = daily.get("time", [])
        if not times:
            raise ValueError(f"Open-Meteo range batch returned empty daily array for "
                             f"{entry.get('latitude')},{entry.get('longitude')} ({start_date}..{end_date})")
        temp, wind, precip = (daily.get(v, []) for v in
                              ("temperature_2m_mean", "wind_speed_10m_max", "precipitation_sum"))
        days = {}
        for i, t in enumerate(times):
            days[t] = {
                "om_temperature": temp[i] if i < len(temp) else None,
                "om_wind_speed": wind[i] if i < len(wind) else None,
                "om_precipitation": precip[i] if i < len(precip) else None,
            }
        out.append(days)
    return out
