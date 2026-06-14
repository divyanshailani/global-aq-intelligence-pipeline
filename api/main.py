"""
IndiaAQ Intelligence - Prediction API

FastAPI endpoint that serves PM2.5 predictions using
the trained GradientBoosting model (v3 + NASA weather).

Modes:
    1. /predict        - Manual: pass features directly
    2. /predict/auto   - Auto: pass station_id + date, API fetches features from DB

Run:
    uvicorn api.main:app --reload
"""

import os
import sys
import joblib
import numpy as np
import pandas as pd
from datetime import date, datetime
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from sqlalchemy import create_engine, text

# Model & Config 

MODEL_PATH = os.path.join(
    os.path.dirname(__file__), "..", "models", "gb_pm25_v3_nasa_weather.pkl"
)

DB_URL = "postgresql://postgres:8765@localhost:5432/indiaaq"

# Feature order must match training exactly
FEATURE_NAMES = [
    "month", "day_of_week", "is_weekend", "day_of_year",
    "lag_1", "lag_2", "lag_3", "lag_7",
    "temperature", "humidity", "wind_speed",
    "no2_value", "co_value", "o3_value", "so2_value",
]

# NAQI breakpoints for PM2.5 (µg/m³)
NAQI_BREAKPOINTS = [
    (0,   30,  "Good"),
    (31,  60,  "Satisfactory"),
    (61,  90,  "Moderate"),
    (91,  120, "Poor"),
    (121, 250, "Very Poor"),
    (251, 500, "Severe"),
]


# Pydantic Models (Request/Response Schemas) 

class ManualPredictRequest(BaseModel):
    """Pass all 15 features manually."""
    month: int = Field(ge=1, le=12, description="Month (1-12)")
    day_of_week: int = Field(ge=0, le=6, description="Day of week (0=Mon, 6=Sun)")
    is_weekend: int = Field(ge=0, le=1, description="Weekend flag (0 or 1)")
    day_of_year: int = Field(ge=1, le=366, description="Day of year (1-366)")
    lag_1: float = Field(description="Yesterday's PM2.5")
    lag_2: float = Field(description="2 days ago PM2.5")
    lag_3: float = Field(description="3 days ago PM2.5")
    lag_7: float = Field(description="7 days ago PM2.5")
    temperature: float = Field(description="Temperature (°C)")
    humidity: float = Field(description="Relative humidity (%)")
    wind_speed: float = Field(description="Wind speed (m/s)")
    no2_value: float = Field(default=0.0, description="Yesterday's NO2")
    co_value: float = Field(default=0.0, description="Yesterday's CO")
    o3_value: float = Field(default=0.0, description="Yesterday's O3")
    so2_value: float = Field(default=0.0, description="Yesterday's SO2")


class AutoPredictRequest(BaseModel):
    """Pass station_id and date, API fetches features from DB."""
    station_id: int = Field(description="Internal station ID")
    target_date: date = Field(description="Date to predict for (YYYY-MM-DD)")


class PredictionResponse(BaseModel):
    """What the API returns."""
    predicted_pm25: float
    naqi_category: str
    confidence_note: str
    features_used: dict


# Helper Functions 

def classify_naqi(pm25_value: float) -> str:
    """Convert PM2.5 value to NAQI category."""
    for low, high, category in NAQI_BREAKPOINTS:
        if low <= pm25_value <= high:
            return category
    if pm25_value > 500:
        return "Severe"
    return "Unknown"


def get_db_engine():
    """Get a SQLAlchemy engine."""
    try:
        return create_engine(DB_URL)
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"Database unavailable: {str(e)}")


def fetch_features_from_db(station_id: int, target_date: date) -> dict:
    """
    Fetch feature values from daily_features table.
    For prediction, we need the PREVIOUS day's row
    (because lag_1 on target_date = value on target_date - 1).
    """
    engine = get_db_engine()
    sql = """
        SELECT month, day_of_week, is_weekend, day_of_year,
               lag_1, lag_2, lag_3, lag_7,
               temperature, humidity, wind_speed,
               no2_value, co_value, o3_value, so2_value
        FROM daily_features
        WHERE station_id = :sid AND date = :dt AND parameter = 'pm25'
    """
    df = pd.read_sql(text(sql), engine, params={"sid": station_id, "dt": target_date})

    if df.empty:
        raise HTTPException(
            status_code=404,
            detail=f"No features found for station {station_id} on {target_date}. "
                   f"Run the ETL pipeline first."
        )

    row = df.iloc[0]
    features = {}
    for col in FEATURE_NAMES:
        val = row.get(col)
        features[col] = 0.0 if pd.isna(val) else float(val)

    return features


# App Lifecycle 

model = None

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Load model on startup, cleanup on shutdown."""
    global model
    if not os.path.exists(MODEL_PATH):
        raise RuntimeError(f"Model not found at {MODEL_PATH}")
    model = joblib.load(MODEL_PATH)
    print(f"Model loaded: {type(model).__name__} ({len(FEATURE_NAMES)} features)")
    yield
    model = None


# FastAPI App 

app = FastAPI(
    title="IndiaAQ Prediction API",
    description="PM2.5 air quality predictions for 478 Indian monitoring stations",
    version="1.0.0",
    lifespan=lifespan,
)

# Allow frontend to call API from any origin (local development)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/")
def root():
    """Health check endpoint."""
    return {
        "service": "IndiaAQ Prediction API",
        "status": "running",
        "model": "GradientBoosting v3 (R²=0.71, MAE=17 µg/m³)",
        "endpoints": ["/predict", "/predict/auto", "/docs"],
    }


@app.post("/predict", response_model=PredictionResponse)
def predict_manual(request: ManualPredictRequest):
    """
    Predict PM2.5 with manually provided features.
    Pass all 15 feature values in the request body.
    """
    features = [getattr(request, name) for name in FEATURE_NAMES]
    feature_df = pd.DataFrame([features], columns=FEATURE_NAMES)

    prediction = float(model.predict(feature_df)[0])
    prediction = max(0.0, round(prediction, 2))

    return PredictionResponse(
        predicted_pm25=prediction,
        naqi_category=classify_naqi(prediction),
        confidence_note="Model: GradientBoosting v3, R²=0.71, MAE=17 µg/m³",
        features_used=dict(zip(FEATURE_NAMES, features)),
    )


@app.post("/predict/auto", response_model=PredictionResponse)
def predict_auto(request: AutoPredictRequest):
    """
    Predict PM2.5 automatically.
    Pass station_id and date — API fetches features from PostgreSQL.
    """
    features_dict = fetch_features_from_db(request.station_id, request.target_date)
    features = [features_dict[name] for name in FEATURE_NAMES]
    feature_df = pd.DataFrame([features], columns=FEATURE_NAMES)

    prediction = float(model.predict(feature_df)[0])
    prediction = max(0.0, round(prediction, 2))

    return PredictionResponse(
        predicted_pm25=prediction,
        naqi_category=classify_naqi(prediction),
        confidence_note="Model: GradientBoosting v3, R²=0.71, MAE=17 µg/m³",
        features_used=features_dict,
    )


@app.get("/stations")
def list_stations():
    """List all available monitoring stations."""
    engine = get_db_engine()
    sql = """
        SELECT s.id, s.name, s.city, s.state, s.latitude, s.longitude,
               COUNT(DISTINCT df.date) as days_with_data
        FROM stations s
        LEFT JOIN daily_features df ON s.id = df.station_id
        GROUP BY s.id, s.name, s.city, s.state, s.latitude, s.longitude
        HAVING COUNT(DISTINCT df.date) > 0
        ORDER BY s.name
    """
    df = pd.read_sql(text(sql), engine)
    return {
        "total_stations": len(df),
        "stations": df.to_dict(orient="records"),
    }


@app.get("/stations/{station_id}/history")
def station_history(station_id: int, days: int = 7):
    """Get recent PM2.5 readings for a station."""
    if days > 90:
        raise HTTPException(status_code=400, detail="Max 90 days")

    engine = get_db_engine()
    sql = """
        SELECT date, value as pm25, temperature, humidity, wind_speed
        FROM daily_features
        WHERE station_id = :sid AND parameter = 'pm25'
        ORDER BY date DESC
        LIMIT :lim
    """
    df = pd.read_sql(text(sql), engine, params={"sid": station_id, "lim": days})
    if df.empty:
        raise HTTPException(status_code=404, detail=f"No data for station {station_id}")

    records = df.to_dict(orient="records")
    for r in records:
        r["naqi_category"] = classify_naqi(r["pm25"]) if r["pm25"] else "Unknown"

    return {
        "station_id": station_id,
        "days": len(records),
        "history": records,
    }
