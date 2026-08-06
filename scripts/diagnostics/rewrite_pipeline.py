import re

with open("/Users/divyanshailani/Desktop/pow-eda-pipeline/scripts/predict_pipeline.py", "r") as f:
    code = f.read()

# 1. Remove predict_horizon
code = re.sub(r'def predict_horizon\(.*?\):.*?return predictions\n\n', '\n', code, flags=re.DOTALL)

# 2. Remove has_v7_models
code = re.sub(r'def has_v7_models\(.*?\):.*?return True\n\n', '\n', code, flags=re.DOTALL)

# 3. Simplify run_predictions loop header
old_run_loop = """    for cc in COUNTRIES:
        if has_v7_models(cc):
            use_v7 = True
            print(f"\\n  {COUNTRY_META[cc]['flag']} {cc}: Generating v7 direct forecasts...")
        else:
            use_v7 = False
            model_path = os.path.join(MODEL_DIR, f"{cc}_pm25_gbr.pkl")
            meta_path = os.path.join(MODEL_DIR, f"{cc}_pm25_meta.json")

            if not os.path.exists(model_path):
                print(f"  ⚠️ No model for {cc}, skipping")
                continue

            model = joblib.load(model_path)
            print(f"\\n  {COUNTRY_META[cc]['flag']} {cc}: Generating v5 forecasts...")"""

new_run_loop = """    for cc in COUNTRIES:
        print(f"\\n  {COUNTRY_META[cc]['flag']} {cc}: Generating v7 direct forecasts...")"""

code = code.replace(old_run_loop, new_run_loop)

# 4. Simplify run_predictions prediction call
old_call = """            if use_v7:
                station_forecast = forecasts.get(sid, {})
                preds = predict_direct_v7(cc, last_row, station_forecast)
                if not preds:
                    continue
            else:
                preds = predict_horizon(model, df, last_row, horizon_days=30, meta_path=meta_path)"""

new_call = """            station_forecast = forecasts.get(sid, {})
            preds = predict_direct_v7(cc, last_row, station_forecast)
            if not preds:
                continue"""

code = code.replace(old_call, new_call)

# 5. Simplify backtest loop header
old_backtest_loop = """    for cc in COUNTRIES:
        if has_v7_models(cc):
            meta_path = os.path.join(V7_MODEL_DIR, f"{cc}_pm25_h1_meta.json")
            model_path = os.path.join(V7_MODEL_DIR, f"{cc}_pm25_h1_gbr.pkl")
        else:
            meta_path = os.path.join(MODEL_DIR, f"{cc}_pm25_meta.json")
            model_path = os.path.join(MODEL_DIR, f"{cc}_pm25_gbr.pkl")"""

new_backtest_loop = """    for cc in COUNTRIES:
        meta_path = os.path.join(V7_MODEL_DIR, f"{cc}_pm25_h1_meta.json")
        model_path = os.path.join(V7_MODEL_DIR, f"{cc}_pm25_h1_gbr.pkl")"""

code = code.replace(old_backtest_loop, new_backtest_loop)

# 6. Add v7 features logic to backtest_recent
old_fill = """        # Fill missing features with 0 and preserve training feature order.
        for col in feature_cols:
            if col not in test_df.columns:
                test_df[col] = 0"""

new_fill = """        # Ensure v7 features are populated
        if "future_temp" in feature_cols:
            test_df["future_temp"] = test_df.get("om_temperature", test_df.get("temperature", 0))
        if "future_wind" in feature_cols:
            test_df["future_wind"] = test_df.get("om_wind_speed", test_df.get("wind_speed", 0))
        if "future_precip" in feature_cols:
            test_df["future_precip"] = test_df.get("om_precipitation", test_df.get("precipitation", 0))

        # Fill missing features with 0 and preserve training feature order.
        for col in feature_cols:
            if col not in test_df.columns:
                test_df[col] = 0"""

code = code.replace(old_fill, new_fill)

with open("/Users/divyanshailani/Desktop/pow-eda-pipeline/scripts/predict_pipeline.py", "w") as f:
    f.write(code)

print("Rewrite complete.")
