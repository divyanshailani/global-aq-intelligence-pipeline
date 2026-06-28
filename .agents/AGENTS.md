# Global AQ Intelligence Pipeline - Project Memory & Rules

When working in the `divyanshailani/global-aq-intelligence-pipeline` workspace, always adhere to the following architectural guidelines, engineering principles, and historical context:

## 1. Project Architecture (V12 / V11.1 - The MASE Crusher)
- **Machine Learning**: Native XGBoost Regressors optimized with Optuna (150 trials).
- **Compute Infrastructure**: Modal Serverless Grid Engine (Distributed hyperparameter sweeps on 32-core Intel Xeon, 16 GB RAM nodes).
- **Data Engineering**: Python, Pandas, Parquet schemas (replacing legacy PostgreSQL for key data), SQLite for Optuna.
- **Frontend**: Zero-runtime Next.js edge-cached dashboard serving static JSON output.

## 2. Core Engineering Principles
- **Direct Multi-Horizon Forecasting**: Use strictly independent models for 1, 7, 14, and 30-day horizons to prevent exponential recursive error (snowballing).
- **Delta Target Transformation**: Models predict "velocity" (`ΔY = Y_t - Y_{t-1}`) instead of absolute PM2.5 levels.
- **Strict Anti-Leakage ("Nuclear Drop")**: Ensure deep memory isolation (`.copy()`). Target variables must never leak into future predictions. E.g., co-pollutants and rolling features must be shifted strictly chronologically.
- **Evaluation Metric**: Optimize for **MASE (Mean Absolute Scaled Error)** and NMAE, avoiding R² which creates a low-variance illusion in clean environments like AU/GB.
- **Phase-Shift Alignment**: Validate physics by strictly comparing `t+h` predictions against `t+h` actual values.

## 3. Physics & Features
- Prioritize thermodynamic and atmospheric physics features, specifically: `rolling_3day_precip`, `aod_volatility_index`, Open-Meteo variables, and NASA VIIRS geospatial fire radius (100km).
- **The GB Exception**: Great Britain routes to legacy V9 models for Day 14 and Day 30 due to overfitting on complex fire/AOD features.

## 4. Current Challenges & Future Frontiers
- **XGBoost Mean Reversion Trap**: The model currently hedges toward averages during rain days (predicting too high). The next frontier involves investigating Quantile Regression or Regime-Switching architectures to solve this.

## 5. Strict Context Boundary
- **DO NOT** cross-pollinate architectural context, code, or design patterns with the `calamity-matrix-core` (LLM/RAG) project. Maintain strict boundary focus on physics-based time-series forecasting.

## 6. Open Issues (State of the Project)
- **#9 [CRITICAL] Target Cascade Leakage & Phase-Shift Evaluation Bug in V12 Pipeline**: Found a bug where `target_1d` was acting as a ghost feature for later horizons. Also, phase-shift evaluation was bugged (comparing prediction vs current day PM2.5 instead of future actuals). Fix is in progress (`v12_tuning.py` and `evaluate_v12_only.py`).
- **#8 [ETL] 14-Day Manual ETL Catchup After AOD Backfill**: Blocked. Waiting on AOD backfill completion to manually catchup ETL without GitHub Actions timeouts.
- **#5 [DATA] Environment State Divergence (Resolved)**: Local DB and Azure DB diverged during AOD backfill.
- **#4 [INFRA] model_registry and predictions Tables Are Empty**: Operational tables and country-specific tables have zero rows; need to determine if they should be dropped or populated.
- **#2 [DATA] 1,464 Stations (35%) Have Zero Rows in daily_features**: 35% of stations lack feature rows, likely due to no raw data or pipeline filtering.

## 7. Codebase Structure
- **`src/`**: Contains core modules like `v12_tuning.py`, `features.py`, `cleaning.py`, `process_aq.py`, `api_fallback_manager.py`, and `aggregations.py`.
- **`scripts/`**: Contains over 70 orchestration and operational scripts, including `run_daily_etl.py`, `predict_pipeline.py`, `evaluate_v11_vs_v12.py`, `evaluate_v12_only.py`, multiple `train_v*.py` scripts, and backfill scripts.

## 8. Azure Cloud Infrastructure & Migration
- **Database Migration**: The project migrated from local to an **Azure PostgreSQL Flexible Server** (`globalaqiserver.postgres.database.azure.com`). The `migrate_db_to_azure.sh` script streams the 1.6M rows directly from local to Azure using a seamless pipe (`pg_dump | psql`).
- **VM Provisioning**: The backend runs on an Ubuntu 24.04 LTS Azure VM (`setup_vm.sh`). It is housed in `/opt/pow-eda-pipeline`.
- **Backend Serving**: A FastAPI dashboard (`scripts.admin_dashboard:app`) is served by Uvicorn, managed by a Systemd service (`globalaqi.service`), and reverse-proxied via **Nginx** pointing to `api.globalaqi.live`.
- **Past Production Issue**: Certbot once updated Nginx to strict host matching on port 443, which Azure NSG blocked. Required adding an Azure Inbound Port 443 rule.

## 9. The Two-Database Paradigm (V11 vs V12 Data Lineage)
- **V11 (Pre-Production / Local DB)**: V11/V11.1 models were trained on the **local Mac PostgreSQL** (`localhost:5432/indiaaq`). This database had critical flaws:
  - `wind_direction` was queried from SQL but **never appended** to the feature array (dead column).
  - AOD nulls were aggressively filled with `.fillna(median())` from the `satellite_aod_features` table, which corrupted the signal by telling the model cloudy days had "average" pollution.
  - The local DB had a 95% NULL rate on columns like `wind_direction` and suffered from data divergence with Azure (Issue #5).
- **V12 (Production / Azure DB → Parquet)**: V12 models are trained on the **Azure PostgreSQL production database**, which was fixed over 2 days (AOD backfill, weather enrichment, ETL catchup). The data flow is:
  1. `export_azure_to_parquet.py` connects directly to Azure DB, JOINs `daily_features` + `stations`, and exports to `data/daily_features_full.parquet` (98 MB, Snappy-compressed).
  2. The Parquet file is uploaded to Modal's persistent volume and used for distributed Optuna training via `src/v12_tuning.py`.
  3. V12 uses 25 features: `['month', 'day_of_week', 'is_weekend', 'day_of_year', 'lag_1', 'lag_2', 'lag_3', 'lag_7', 'lag_14', 'lag_21', 'lag_30', 'roll_3_mean', 'roll_7_mean', 'roll_3_std', 'roll_14_mean', 'roll_30_mean', 'roll_14_std', 'om_temperature', 'om_wind_speed', 'om_precipitation', 'om_aerosol_optical_depth', 'rolling_3day_precip', 'aod_volatility_index', 'latitude', 'longitude']`.
- **V12 Thermodynamic Phase-Shift**: Unlike V11 (which fetched 16-day future weather forecasts at inference), V12 maps *today's* atmospheric state directly to PM2.5 at `t+h`. No future weather needed at inference time.
- **V12 Models Location**: All 16 retrained models saved as `model.json` (XGBoost native) in `models/v12/{CC}/horizon_{h}/model.json` (CC = AU, GB, IN, US; h = 1, 7, 14, 30).

## 10. Deep Scan: Database Health Comparison (June 28, 2026)
- **Local DB (Pre-Production)**: 1,631,267 `daily_features` rows. Weather nulls at 1.1%. `rolling_3day_precip` / `aod_volatility_index` nulls at 0.2%. Most tables truncated (0 rows in `raw_measurements`, `stations`, etc.). `wind_direction` column does NOT exist. `satellite_aod_features` has 3.7M rows.
- **Azure DB (Production)**: 1,632,146 `daily_features` rows. **0% nulls** on `om_temperature`, `om_wind_speed`, `om_precipitation`, `rolling_3day_precip`, `aod_volatility_index`. Full `raw_measurements` (18.4M rows), `stations` (4,193), `prediction_log` (191K). `wind_direction` column does NOT exist.
- **Parquet (V12 Training)**: 1,632,146 rows × 32 cols. Matches Azure DB. 2,730 stations (vs 4,193 in Azure — the JOIN drops stations with no features). 16,923 rows (1.0%) have NULL `country_code` from 1,158 orphan stations.
- **AOD Null Rate**: ~33% across ALL environments. This is a physics constraint (cloud cover blocks satellite AOD), not a data bug. **India has 63.5% AOD null rate** (monsoon), nearly 2x other countries.

## 11. Data Distribution & Holdout Statistics
- **Country data imbalance**: US dominates with 1.4M rows (88%), IN has 60K (3.7%), GB has 34K (2.1%), AU has 84K (5.2%).
- **Rows per station**: US=912, AU=488, IN=112, GB=100. India and GB have very thin per-station histories.
- **Holdout period** (Jan 2026+): GB=26,128 rows (94/station avg), IN=7,853 (17/station), AU=4,402 (39/station), US=3,496 (9.7/station). **US holdout starts March 21, not January 1.**
- **Evaluable rows for long horizons** (after target shift cutoff): US h=14d has only **83 rows**, US h=30d has **82 rows**. AU h=30d has only **207 rows**. GB is the only statistically trustworthy country for long-horizon evaluation (18K+ rows for h=30d).

## 12. Known Script Bugs & Architectural Issues
- **`v12_tuning.py` — Train-on-All-Data**: The final model trains on 100% of data (`final_model.fit(X, y)`), meaning the holdout period (Jan 2026+) used by `evaluate_v12_only.py` was already seen during training. The model saw holdout *features* (not targets). This partially deflates evaluation error.
- **`evaluate_v12_only.py` — No Nuclear Drop in Eval**: Creates ALL `target_*` columns in the dataframe, then pulls features from the model's `feature_names`. If old contaminated models are loaded, `target_1d` gets silently fed as a feature with real future values. Clean retrained models are safe, but the script is fragile.
- **`evaluate_v11_vs_v12.py` — Phase-Shift Merge Bug**: V11 predictions target `date + h` but are scored against `date`'s PM2.5 (same-day value). V12 is correctly scored via `target_date` remapping. This **unfairly penalizes V11** in the comparison, partially inflating V12's "9/9 wins" result.
- **V11 `train_v11_aod_global.py` Flaws**: (1) AOD median-fill corruption from `satellite_aod_features`. (2) `wind_direction` hardcoded to 0.0, never used. (3) `tune_v11_per_country.py` tunes only on h=1 then applies to all horizons. (4) Main training optimizes MASE but per-country tuning optimizes MAE (inconsistent objectives).

## 13. V12 Evaluation Ground Rules
- **Only GB has statistically significant long-horizon evaluation data.** Do NOT draw conclusions from US h=14d/30d or AU h=30d metrics.
- **V11.1 README metrics (MAE=9.76 for IN 1d, Acc=74.58%) are cross-validation metrics** (optimistically biased). V12 holdout metrics are pure out-of-sample (pessimistically honest). Do NOT compare them directly.
- **India's 63.5% AOD null rate** means any model heavily relying on `om_aerosol_optical_depth` will underperform in India relative to other countries.
- **Always re-export Parquet before evaluation** if Azure DB has been updated, to ensure evaluation data is fresh.

## 14. V12 Pure Evaluation Results (Challenger Engine)
- **Engine Evolution**: The legacy evaluation script (`evaluate_v12_only.py`) was deprecated and replaced with `evaluate_v12_pure.py`, which enforces 1) a 3-layer Nuclear Drop, 2) strict Phase-Shift Alignment, and 3) Honest MASE against a persistence baseline. No AOD imputation is used; XGBoost `hist` natively handles NaN.
- **Performance**: 16/16 Models Beat Persistence. All 16 country/horizon V12 models achieved MASE < 1.0.
- **GB Dominance**: Great Britain demonstrated exceptional stability with MASE 0.17 at h=14 and h=30 (83% better than persistence) and high Accuracy (~88%), making it the strongest model overall.
- **IN Resilience**: Despite the 63.5% AOD null rate (monsoon blinding), India achieved MASE 0.5185 at h=30. Accuracy remains in the 42-50% range, but the model accurately captures trend direction during transitions.
- **US Mean Reversion**: 2x2 forecast grids confirm that long-horizon (14d/30d) US models suffer from a mean reversion trap, frequently hedging toward ~12 µg/m³ during true spikes of 60 µg/m³.
- **Error Decay Reality**: The error decay charts prove that error scales physically with horizon for US and GB. India's MAE uniquely *decreases* at longer horizons (34.6 → 27.1), which is a correct reflection of transitioning from high-volatility winter into the stable low-PM2.5 monsoon season.
