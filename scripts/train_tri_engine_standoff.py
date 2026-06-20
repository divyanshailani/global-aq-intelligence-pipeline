import os
import sys
import time
import warnings
import joblib
import numpy as np
import pandas as pd
import psycopg2

from sklearn.ensemble import GradientBoostingRegressor
import xgboost as xgb
import lightgbm as lgb
from sklearn.metrics import mean_absolute_error, mean_squared_error, r2_score

warnings.filterwarnings("ignore")

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from src.config import DB_CONFIG

HORIZONS = [1, 7, 14, 30]
COUNTRIES = ["IN", "GB"]
CHALLENGER_XGB_DIR = os.path.join(os.path.dirname(__file__), "..", "models", "challenger_xgb")
CHALLENGER_LGBM_DIR = os.path.join(os.path.dirname(__file__), "..", "models", "challenger_lgbm")

def load_base_data(conn, country_code):
    sql = """
        SELECT station_id, date, value,
               om_temperature as future_temp,
               om_wind_speed as future_wind,
               om_precipitation as future_precip
        FROM daily_features
        WHERE country_code = %s
          AND parameter = 'pm25'
          AND value IS NOT NULL
        ORDER BY station_id, date
    """
    df = pd.read_sql(sql, conn, params=(country_code,))
    df["date"] = pd.to_datetime(df["date"])
    return df

def build_v8_features(df, horizon):
    df_v8 = df.copy()
    df_v8 = df_v8.sort_values(["station_id", "date"])
    
    lag_col = f"pm25_lag_{horizon}"
    df_v8[lag_col] = df_v8.groupby("station_id")["value"].shift(horizon)
    
    roll_mean = df_v8.groupby("station_id")["value"].rolling(3).mean().reset_index(level=0, drop=True)
    df_v8["pm25_rolling_mean_3d"] = roll_mean.groupby(df_v8["station_id"]).shift(horizon)
    
    roll_std = df_v8.groupby("station_id")["value"].rolling(3).std().reset_index(level=0, drop=True)
    df_v8["pm25_rolling_std_3d"] = roll_std.groupby(df_v8["station_id"]).shift(horizon)
    
    df_v8["month"] = df_v8["date"].dt.month
    df_v8["day_of_year"] = df_v8["date"].dt.dayofyear
    df_v8["day_of_week"] = df_v8["date"].dt.dayofweek
    
    df_v8 = df_v8.dropna(subset=[lag_col, "pm25_rolling_mean_3d", "pm25_rolling_std_3d"])
    
    features = [
        lag_col, "pm25_rolling_mean_3d", "pm25_rolling_std_3d",
        "month", "day_of_year", "day_of_week",
        "future_temp", "future_wind", "future_precip"
    ]
    return df_v8, features

def temporal_split(df, target_col, test_ratio=0.20):
    train_parts, test_parts = [], []
    for sid, group in df.groupby("station_id"):
        group = group.sort_values("date")
        n = len(group)
        split_idx = int(n * (1 - test_ratio))
        if split_idx < 10 or (n - split_idx) < 3:
            continue
        split_date = group.iloc[split_idx]["date"]
        train_mask = group["date"] < split_date
        test_mask  = group["date"] >= split_date
        train_parts.append(group[train_mask])
        test_parts.append(group[test_mask])
    if not train_parts: return pd.DataFrame(), pd.DataFrame()
    return pd.concat(train_parts, ignore_index=True), pd.concat(test_parts, ignore_index=True)

def train_tri_engine(conn, country_code, horizon):
    print(f"\n  ── {country_code}  h={horizon:>2}d ──")
    df = load_base_data(conn, country_code)
    if len(df) < 100: return None

    df_h, features = build_v8_features(df, horizon)
    target_col = "value"
    train, test = temporal_split(df_h, target_col)
    if train.empty or test.empty: return None

    y_test = test[target_col].copy()
    y_naive = test[f"pm25_lag_{horizon}"].copy()
    naive_mae = mean_absolute_error(y_test, y_naive)

    X_train = train[features].copy()
    y_train = train[target_col].copy()
    X_test  = test[features].copy()

    medians = {}
    for col in features:
        med = X_train[col].median()
        if hasattr(med, '__len__'):
            med = med.iloc[0] if len(med) > 0 else 0.0
        medians[col] = med if not (isinstance(med, float) and pd.isna(med)) else 0.0
        X_train[col] = X_train[col].fillna(medians[col])
        X_test[col]  = X_test[col].fillna(medians[col])

    X_train = X_train.replace([np.inf, -np.inf], 0)
    X_test  = X_test.replace([np.inf, -np.inf], 0)

    mean_y = np.mean(y_test)
    results = []

    # 1. GBR
    t0 = time.time()
    gbr = GradientBoostingRegressor(n_estimators=300, max_depth=5, learning_rate=0.05, subsample=0.8, min_samples_leaf=10, random_state=42)
    gbr.fit(X_train, y_train)
    gbr_time = time.time() - t0
    y_pred_gbr = gbr.predict(X_test)
    gbr_mae = mean_absolute_error(y_test, y_pred_gbr)
    gbr_nmae = gbr_mae / mean_y if mean_y > 0 else 0
    gbr_mase = gbr_mae / naive_mae if naive_mae > 0 else 0
    results.append({"Engine": "GBR", "MAE": gbr_mae, "NMAE": gbr_nmae, "MASE": gbr_mase, "Time": gbr_time, "NativeLoss": "N/A"})
    print("GBR complete.")

    os.makedirs(CHALLENGER_XGB_DIR, exist_ok=True)
    os.makedirs(CHALLENGER_LGBM_DIR, exist_ok=True)

    # 2. XGBoost
    t0 = time.time()
    model_xgb = xgb.XGBRegressor(n_estimators=300, max_depth=5, learning_rate=0.05, subsample=0.8, eval_metric="mae", early_stopping_rounds=10, random_state=42)
    model_xgb.fit(X_train, y_train, eval_set=[(X_test, y_test)], verbose=False)
    xgb_time = time.time() - t0
    y_pred_xgb = model_xgb.predict(X_test)
    xgb_mae = mean_absolute_error(y_test, y_pred_xgb)
    xgb_nmae = xgb_mae / mean_y if mean_y > 0 else 0
    xgb_mase = xgb_mae / naive_mae if naive_mae > 0 else 0
    xgb_loss = model_xgb.best_score
    results.append({"Engine": "XGBoost", "MAE": xgb_mae, "NMAE": xgb_nmae, "MASE": xgb_mase, "Time": xgb_time, "NativeLoss": xgb_loss})
    joblib.dump(model_xgb, os.path.join(CHALLENGER_XGB_DIR, f"{country_code}_pm25_h{horizon}_xgb.pkl"))
    print("XGBoost complete.")

    # 3. LightGBM
    t0 = time.time()
    model_lgb = lgb.LGBMRegressor(n_estimators=300, max_depth=5, learning_rate=0.05, subsample=0.8, min_child_samples=10, random_state=42)
    model_lgb.fit(X_train, y_train, eval_set=[(X_test, y_test)], eval_metric="l1", callbacks=[lgb.early_stopping(10, verbose=False)])
    lgb_time = time.time() - t0
    y_pred_lgb = model_lgb.predict(X_test)
    lgb_mae = mean_absolute_error(y_test, y_pred_lgb)
    lgb_nmae = lgb_mae / mean_y if mean_y > 0 else 0
    lgb_mase = lgb_mae / naive_mae if naive_mae > 0 else 0
    lgb_loss = model_lgb.best_score_["valid_0"]["l1"] if model_lgb.best_score_ else "N/A"
    results.append({"Engine": "LightGBM", "MAE": lgb_mae, "NMAE": lgb_nmae, "MASE": lgb_mase, "Time": lgb_time, "NativeLoss": lgb_loss})
    joblib.dump(model_lgb, os.path.join(CHALLENGER_LGBM_DIR, f"{country_code}_pm25_h{horizon}_lgbm.pkl"))
    print("LightGBM complete.")

    return results

def main():
    conn = psycopg2.connect(**DB_CONFIG)
    all_results = []
    
    for cc in COUNTRIES:
        for h in HORIZONS:
            res = train_tri_engine(conn, cc, h)
            if res:
                for r in res:
                    r["Country"] = cc
                    r["Horizon"] = h
                    all_results.append(r)
    conn.close()

    print("\n### Tri-Engine Comparison Table (V9 Candidates)")
    print("| Country | Horizon | Engine | Compute Time (s) | Native Loss | MAE | NMAE | MASE |")
    print("| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |")
    for r in all_results:
        nl = f"{r['NativeLoss']:.4f}" if isinstance(r['NativeLoss'], (int, float)) else r['NativeLoss']
        print(f"| {r['Country']} | {r['Horizon']} | {r['Engine']} | {r['Time']:.2f}s | {nl} | {r['MAE']:.4f} | {r['NMAE']:.4f} | {r['MASE']:.4f} |")

if __name__ == "__main__":
    main()
