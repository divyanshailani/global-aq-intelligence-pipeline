import os
import sys
import json
import numpy as np
import pandas as pd
import psycopg2
import xgboost as xgb
from sklearn.metrics import mean_absolute_error

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from src.config import DB_CONFIG
from scripts.train_v9_4_xgboost import load_base_data, temporal_split, haversine_dist

def calculate_bearing(lat1, lon1, lat2, lon2):
    lat1, lon1, lat2, lon2 = map(np.radians, [lat1, lon1, lat2, lon2])
    dlon = lon2 - lon1
    x = np.sin(dlon) * np.cos(lat2)
    y = np.cos(lat1) * np.sin(lat2) - (np.sin(lat1) * np.cos(lat2) * np.cos(dlon))
    initial_bearing = np.arctan2(x, y)
    initial_bearing = np.degrees(initial_bearing)
    compass_bearing = (initial_bearing + 360) % 360
    return compass_bearing

def build_v10_features(df, viirs, horizon):
    df_v10 = df.copy()
    
    df_v10["fire_density_100km"] = 0.0
    df_v10["fire_radiative_power_total"] = 0.0
    df_v10["upwind_fire_power"] = 0.0
    
    if not viirs.empty:
        for date, group in df_v10.groupby("date"):
            viirs_date = viirs[viirs["acq_date"] == date]
            if viirs_date.empty: continue
            for idx, row in group.iterrows():
                dists = haversine_dist(row["station_lat"], row["station_lon"], viirs_date["fire_lat"].values, viirs_date["fire_lon"].values)
                mask = dists <= 100.0
                if np.any(mask):
                    df_v10.at[idx, "fire_density_100km"] = float(np.sum(mask))
                    df_v10.at[idx, "fire_radiative_power_total"] = float(np.sum(viirs_date["brightness"].values[mask]))
                    
                    if "wind_direction" in row and pd.notna(row["wind_direction"]):
                        wind_dir = row["wind_direction"]
                        fire_lats = viirs_date["fire_lat"].values[mask]
                        fire_lons = viirs_date["fire_lon"].values[mask]
                        bearings = calculate_bearing(row["station_lat"], row["station_lon"], fire_lats, fire_lons)
                        
                        # Abs difference mod 360
                        diff = np.abs((bearings - wind_dir + 180) % 360 - 180)
                        upwind_mask = diff <= 90.0
                        
                        upwind_brightness = viirs_date["brightness"].values[mask][upwind_mask]
                        df_v10.at[idx, "upwind_fire_power"] = float(np.sum(upwind_brightness))
                        
    df_v10 = df_v10.sort_values(["station_id", "date"])
    
    lag_col = f"pm25_lag_{horizon}"
    df_v10[lag_col] = df_v10.groupby("station_id")["value"].shift(horizon)
    
    lag_col_next = f"pm25_lag_{horizon+1}"
    df_v10[lag_col_next] = df_v10.groupby("station_id")["value"].shift(horizon+1)
    
    df_v10["pm25_ema_3d"] = df_v10.groupby("station_id")["value"].transform(lambda x: x.ewm(span=3, adjust=False).mean()).shift(horizon)
    
    df_v10["month"] = df_v10["date"].dt.month
    df_v10["day_of_year"] = df_v10["date"].dt.dayofyear
    df_v10['month_sin'] = np.sin(2 * np.pi * df_v10["month"] / 12)
    df_v10['month_cos'] = np.cos(2 * np.pi * df_v10["month"] / 12)
    df_v10['day_of_year_sin'] = np.sin(2 * np.pi * df_v10["day_of_year"] / 365.25)
    df_v10['day_of_year_cos'] = np.cos(2 * np.pi * df_v10["day_of_year"] / 365.25)
    
    df_v10["pm25_momentum"] = df_v10[lag_col] - df_v10[lag_col_next]
    df_v10["future_temp_momentum"] = df_v10.groupby("station_id")["future_temp"].diff(1)
    df_v10["future_wind_momentum"] = df_v10.groupby("station_id")["future_wind"].diff(1)
    
    if "wind_direction" in df_v10.columns:
        wd = df_v10["wind_direction"].fillna(0)
        df_v10["wind_u"] = np.cos(wd * np.pi / 180)
        df_v10["wind_v"] = np.sin(wd * np.pi / 180)
        
    df_v10["fire_wind_interaction"] = df_v10["fire_radiative_power_total"] * df_v10["future_wind"]
    
    # NEW: Stagnation Index (V10.1 Log-Scaled)
    df_v10["stagnation_index"] = np.log1p(df_v10["fire_radiative_power_total"] / (df_v10["future_precip"] + df_v10["future_wind"] + 0.1))
    
    # Target Delta
    df_v10["target_delta"] = df_v10["value"] - df_v10[lag_col]
    
    df_v10 = df_v10.dropna(subset=["value", lag_col, lag_col_next, "pm25_ema_3d", "future_temp_momentum", "future_wind_momentum"])
    
    features = [
        lag_col, "pm25_ema_3d",
        "month_sin", "month_cos", "day_of_year_sin", "day_of_year_cos",
        "future_temp", "future_wind", "future_precip",
        "pm25_momentum", "future_temp_momentum", "future_wind_momentum",
        "fire_density_100km", "fire_radiative_power_total", 
        "fire_wind_interaction",
        "stagnation_index", "upwind_fire_power"
    ]
    if "wind_direction" in df_v10.columns:
        features.extend(["wind_u", "wind_v"])
        
    return df_v10, features

def main():
    print("Initializing V10.1 Log-Scaled Stagnation & Hemispheric Upwind Engine for IN (h=1)...")
    conn = psycopg2.connect(**DB_CONFIG)
    df_in, viirs_data = load_base_data(conn, "IN")
    conn.close()
    
    df_h, features = build_v10_features(df_in, viirs_data, 1)
    train, test = temporal_split(df_h, "value", test_ratio=0.20)
    
    X_train, y_train = train[features], train["target_delta"]
    X_test, y_test_delta = test[features], test["target_delta"]
    
    y_test_true = test["value"]
    y_test_lag = test["pm25_lag_1"]
    y_train_true = train["value"]
    y_train_lag = train["pm25_lag_1"]
    
    print(f"Training V10 Model with features: {features}")
    
    params = {
        'n_estimators': 300,
        'learning_rate': 0.05,
        'max_depth': 6,
        'subsample': 0.8,
        'colsample_bytree': 0.8,
        'random_state': 42,
        'tree_method': 'hist'
    }
    model = xgb.XGBRegressor(**params)
    model.fit(X_train, y_train, eval_set=[(X_test, y_test_delta)], verbose=False)
    
    test_pred_delta = model.predict(X_test)
    y_pred = np.maximum(0, test_pred_delta + y_test_lag.values)
    
    mae = mean_absolute_error(y_test_true, y_pred)
    
    # MASE
    naive_err = mean_absolute_error(y_train_true, y_train_lag)
    mase = mae / naive_err if naive_err > 0 else 0
    
    test_df = test.copy()
    test_df["predicted"] = y_pred
    test_df["abs_error"] = np.abs(test_df["value"] - test_df["predicted"])
    
    print("\n" + "="*50)
    print(f"V10 Overall Mean Absolute Error: {mae:.2f} µg/m³")
    print(f"V10 Overall MASE: {mase:.4f}")
    print("="*50)
    
    print("\n--- Extreme Spike Magnitude Slice (True PM2.5 > 150) ---")
    extreme_mask = test_df["value"] > 150
    extreme_test = test_df[extreme_mask]
    
    if not extreme_test.empty:
        extreme_mae = extreme_test["abs_error"].mean()
        print(f"V10 MAE on Extreme Spikes (>150): {extreme_mae:.2f} µg/m³ (N={len(extreme_test)})")
        print(f"(For reference, V9.4 MAE on this slice was 87.48 µg/m³)")
    else:
        print("No extreme spikes found in the test set.")
        
    print("\nSimulation Complete.")

if __name__ == "__main__":
    main()
