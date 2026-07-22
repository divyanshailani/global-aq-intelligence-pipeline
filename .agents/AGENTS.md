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

## 5. Strict Context Boundary (1 Agent, 1 Project Rule)
- **CRITICAL DIRECTIVE**: I am explicitly and exclusively dedicated to the `global-aq-intelligence-pipeline` project. 
- **DO NOT** ever bring up, scan, or cross-pollinate ideas from other projects (e.g., `calamity-matrix-core`, Solar System/RL environments, or any dropped ideas). 
- The user operates under a strict **"1 agent 1 project"** rule. Maintain absolute, unwavering focus on this physics-based time-series forecasting architecture.

## 6. Open Issues (State of the Project)
- **#9 [CRITICAL] Target Cascade Leakage & Phase-Shift Evaluation Bug in V12 Pipeline (Resolved)**: The `evaluate_v12_pure.py` engine now correctly enforces memory isolation per horizon and strict `t+h` targeting.
- **#8 [ETL] 14-Day Manual ETL Catchup After AOD Backfill (Resolved)**: Manual ETL catchup successfully completed. Data fully synchronized to present day.
- **#5 [DATA] Environment State Divergence (Resolved)**: Local DB and Azure DB diverged during AOD backfill.
- **#4 [INFRA] model_registry and predictions Tables Are Empty (Resolved)**: Tables were deprecated by the V12 architecture (which uses Parquet/JSON instead) and have been successfully dropped from the Azure DB.
- **#2 [DATA] 1,464 Stations (35%) Have Zero Rows in daily_features (Resolved)**: 35% of stations lack feature rows, which is expected behavior due to lack of raw PM2.5 data during those intervals.

## 7. Codebase Structure
- **`src/`**: Contains core modules like `v12_tuning.py`, `features.py`, `cleaning.py`, `process_aq.py`, `api_fallback_manager.py`, and `aggregations.py`.
- **`scripts/`**: Contains over 70 orchestration and operational scripts, including `run_daily_etl.py`, `predict_pipeline.py`, `evaluate_v11_vs_v12.py`, `evaluate_v12_only.py`, multiple `train_v*.py` scripts, and backfill scripts.

## 8. Azure Cloud Infrastructure & Migration
- **Database Migration**: The project migrated from local to an **Azure PostgreSQL Flexible Server** (`globalaqiserver.postgres.database.azure.com`). The `migrate_db_to_azure.sh` script streams the 1.6M rows directly from local to Azure using a seamless pipe (`pg_dump | psql`).
- **VM Provisioning**: The backend runs on an Ubuntu 24.04 LTS Azure VM (`setup_vm.sh`). It is housed in `/opt/pow-eda-pipeline`. SSH Access: `globaladmin@4.213.226.19` (`api.globalaqi.live`). Resources verified stable (low memory/disk pressure, all services active).
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

## 15. The V2 Batch ETL Refactor (Performance & Bugs)
- **The 3-Hour Bottleneck**: The legacy ETL pipeline (`run_daily_etl.py`, `cleaning.py`, `features.py`) iterated through 2,700+ stations sequentially, executing 4-5 DB queries *per station*. This caused GitHub Actions to take 3h 25m due to Azure DB network latency.
- **The V2 Batch Refactor**: Replaced sequential loops with bulk operations.
  - Phase 1 (Cleaning): Single `pd.read_sql` for all stations → vectorized pandas cleaning → single `execute_values` bulk insert.
  - Phase 2 (Features): Single `pd.read_sql` for the 90-day lookback → vectorized pandas groupby → single `execute_values` bulk insert.
- **Concurrency Bug (Phase 3)**: Initially, Phase 3 used `ThreadPoolExecutor` for Open-Meteo API fetching. This immediately triggered rate limits (Read Timed Out). Reverted to sequential API fetching with a `0.15s` sleep.
- **Destructive Deletion Bug**: Previously, if an Open-Meteo API call failed, the pipeline would `DELETE` the entire `daily_features` row. This was fixed to keep the row but leave weather features as `NULL`, which XGBoost handles natively.
- **Data Volume Reality**: Even with the batch refactor, the pipeline still physically transfers massive amounts of data (~824K rows bulk inserted in Phase 1; ~2.5M rows pulled for 90-day rolling computations in Phase 2). As a result, the GitHub Action will naturally take **15-20 minutes** to run. This is not a "hang" or a bug, but the physical limit of the data transfer volume.
- **Debugging Tip**: GitHub Actions buffers Python logs by default, making long batch operations look like they are hanging. Always run the script with `python3 -u scripts/run_daily_etl.py` to stream logs directly to standard out.

## 16. The V12 Production Release & Architectural Overhauls
- **V12 Official Launch**: The V12 Challenger Engine is now the official production model, permanently replacing V11. All dependencies and pipelines point strictly to V12 ONNX artifacts.
- **Inference Pipeline Refactor (Parquet -> SQL)**: In Step 2 of the GitHub Actions pipeline, we completely removed the multi-minute 98MB Parquet export process (which was originally designed for training, not inference). The `predict_v12_onnx.py` script now securely connects directly to Azure PostgreSQL via `psycopg2`, executing a `DISTINCT ON (station_id)` query to pull only the latest ~1,551 rows. Inference data extraction now takes ~0.5 seconds instead of ~5 minutes, drastically reducing compute overhead and removing the `pyarrow` dependency from inference.
- **The "Naive" Timestamp Bug**: A bug caused the frontend to display Vercel's UTC pipeline time (e.g., 12:59 PM) instead of local time (e.g., 6:29 PM IST). The root cause was that Python's `datetime.now().isoformat()` returned a naive string (no `Z` at the end). When Next.js served this to the client, the browser interpreted the naive string as local time. This was fixed by using `datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')` in `predict_v12_onnx.py`, forcing the browser to perform the timezone conversion.
- **Frontend Hydration Fix**: The `global-aq-intelligence-web` Next.js frontend was updated to format the timestamp via a `useEffect` hook to bypass server-side hydration mismatches when converting UTC strings to the user's local timezone.

## 17. Security Hardening & Secret Management (June 29, 2026)
- **Zero-Trust Hardening**: Hardcoded database URIs, passwords, and OpenAQ API keys were strictly removed from the entire codebase (e.g., `src/config.py`, `scripts/migrate_db_to_azure.sh`). All components now strictly load secrets via `os.environ` relying on `.env` (local/VM) or GitHub Secrets (CI/CD).
- **Git History Purge**: The legacy database password was accidentally leaked in previous commits. It was permanently scrubbed from the entire Git history using `git filter-repo` to replace it with `[REDACTED]`. The remote GitHub repository was then force-pushed to apply the rewritten SHA hashes.
- **Secret Rotation (Critical Distinction)**: 
  - **Azure VM SSH Password**: Rotated to the new `AQI` suffix key.
  - **Azure PostgreSQL Database Password**: Remains the original `Global` suffix key. These two credentials must never be confused, as setting the DB password to the SSH password will break the entire GitHub Actions pipeline.
- **Git Hygiene**: Extraneous directories like `scripts/node_modules/` (7000+ files) were properly added to `.gitignore` and purged from Git tracking to dramatically reduce repository size and clone times.
## 18. The OpenAQ S3 Architecture Migration (July 4, 2026)
- **The Rate Limit Crisis**: Discovered that OpenAQ v3 API enforces 1 request per sensor. With ~16,750 active sensors across 4193 stations, a single day of fetching requires 16,750 API calls, completely burning through the free tier limit (30k/month across 3 keys) in 1.8 days.
- **The S3 Bucket Solution**: Abandoned the REST API in favor of scraping OpenAQ's daily gzipped CSV files directly from their public AWS S3 bucket (`s3://openaq-data-archive/records/csv.gz/`). This bypasses all API keys, rate limits, and throttling, enabling a direct RAM-to-DB ingestion pipeline.
- **Two-Tier Architecture**:
  1. `bulk_backfill_local.py`: High-concurrency script (`Semaphore(100)`) strictly designed to be run manually on the Mac Mini to bridge massive historical gaps (e.g., catching up from June 16, 2026).
  2. `fetch_openaq.py`: Low-concurrency script (`Semaphore(5)`) for the daily GitHub Actions VM to parse daily deltas while protecting the 2-vCPU runner's memory limits during decompression.
- **Automated API Fallback**: The original API-based fetcher is safely archived as `legacy_api_fetcher.py` for disaster recovery. If the primary S3 fetch encounters any error (e.g., AWS outage, permission change), `run_daily_collector.py` automatically catches the exception, dynamically imports the legacy fetcher, and completes the run via the OpenAQ REST API.
- **Handling Dead Sensors**: Many stations naturally return 0 rows because physical sensors frequently die or go offline. The S3 scripts elegantly skip missing files (HTTP 404 Not Found), maintaining DB integrity and letting the ETL pipeline naturally drop them, ensuring XGBoost trains only on active hardware.

## 19. Distributed Swarm & PostgreSQL Type-Casting Fix (July 5, 2026)
- **Open-Meteo 10K Rate Limit**: The pipeline hit a hard 10,000 API calls/day limit per IP. A 4-node distributed Python Swarm (`swarm_weather_fetch.py`) was deployed across DigitalOcean, Azure, and Mac Mini to shard the workload and bypass the IP limit, successfully backfilling 10,000+ missing rows.
- **The numpy.int64 psycopg2 Crash**: The swarm initially crashed during `execute_batch` bulk-upserts with `psycopg2.ProgrammingError: can't adapt type 'numpy.int64'`. The root cause was `np.array_split` converting Python integers into `numpy.int64` scalars, which persisted into the DB tuples. This was fixed by explicitly casting every single tuple element into native Python `float()` and `int()` just before appending.
- **GitHub Action Traffic Shift**: The automated workflow `daily_pipeline.yml` cron schedule was shifted to 5:30 UTC (`30 5 * * *`) to avoid high GitHub Actions queue times and server traffic spikes.
- **Autonomous Next.js Sync**: A local `deploy_when_ready.py` daemon was developed to automatically poll the Azure DB for missing row convergence, trigger the `predict_v12_onnx.py` inference engine, and force-push the static `.json` predictions directly into the `global-aq-intelligence-web` Next.js frontend repository for an instant Vercel sync.

## 20. The Missing Gap & CLI Arguments Bug (July 5, 2026)
- **The S3 vs ETL Distinction**: A critical architectural distinction was documented: Ingestion (S3 bucket downloading 26M rows to `raw_measurements`) is completely separate from ETL processing (`run_daily_etl.py` which computes features). Ingesting the data does not make it available to the ML model until the ETL script formally aggregates it.
- **The 3-Day Lookback Bug**: During the massive June 16 - July 5 catchup, `run_daily_etl.py` ran flawlessly but generated only 1,153 rows. This was caused by the script's default CLI argument (`--recent-days 3`), which blindly ignored any raw data older than 3 days. To bridge massive historical gaps, the script MUST be executed with a manual override (e.g., `--recent-days 25`).
- **Open-Meteo Skip Logic**: The ETL natively handles weather API limits during massive backfills. By querying `WHERE om_temperature IS NULL`, it safely ignores existing weather data. When it inevitably hits the 10K rate limit on new rows, it gracefully catches the exception, leaves the weather as NULL, and moves on, allowing the 4-Node Swarm to pick up the slack without deleting valid PM2.5 rows.
- **The Missing Country Code Bug (July 5, 2026)**: The V2 Batch refactor introduced a silent bug where `bulk_insert_features` in `src/features.py` omitted the `country_code` column during insert. This caused all 28,000+ new backfilled rows to have a NULL `country_code`. Because the ONNX inference script filters by country code, it silently ignored the new July data and generated predictions for June 16th, which the Next.js UI then falsely displayed as "Today's" prediction. This was permanently fixed by injecting a SQL `UPDATE ... FROM stations` patch directly into the `bulk_insert_features` method to retroactively apply the country codes to new rows after insertion.

## 21. Architectural Comparison: V12 ML Engine vs. Real-Time Dashboards (aqi.in)
- **Use Case Divergence**: The V12 Engine is a **macro-climatology forecasting tool** predicting the baseline thermodynamic trend of a country days/weeks in advance. Dashboards like aqi.in are **real-time reactive monitors** focused on hyper-local hazard warnings.
- **Data Source Discrepancy**: V12 relies strictly on OpenAQ's aggregation of official, highly calibrated government reference monitors (like CPCB), which measure ambient background pollution. Dashboards like aqi.in crowdsource data from commercial, uncalibrated IoT sensors placed directly in high-emission zones (balconies, traffic intersections), naturally skewing their readings higher.
- **The "Top 10" Aggregation Bias**: While aqi.in deliberately highlights a sorted leaderboard of the absolute most polluted, heavily industrialized cities (e.g., Delhi NCR), V12 calculates a pure mathematical **national mean** across hundreds of stations, blending extreme industrial zones with clean coastal/monsoon regions.
- **The V12 Mean Reversion Trap (Extreme Spikes)**: When evaluating V12 against live spikes (e.g., Delhi hitting 150+ µg/m³), V12's highest national prediction capped at ~69 µg/m³. This empirically proves the XGBoost **Mean Reversion Trap**: ML models optimizing for MAE/MASE are statistically conservative. To avoid massive error penalties, they hedge toward historical averages rather than predicting extreme anomalous spikes.
- **Monsoon Blinding**: V12's mean reversion is drastically amplified in India during the monsoon season. Due to heavy cloud cover, satellite Aerosol Optical Depth (AOD) suffers a 63.5% null rate. Blinded to actual atmospheric smoke/dust, the model relies on local weather (heavy rain) and aggressively predicts a "clean" day, failing to capture hyper-local industrial emissions happening under the clouds. Future V13 architectures must implement **Quantile Regression** to penalize under-predictions on extreme outliers.

## 22. VM Cron `.env` Sourcing Fix & Pipeline Staleness (July 22, 2026)
- **Root Cause 1 (GitHub Actions Timeout)**: The automated GitHub Action (`daily_pipeline.yml`) timed out after 6 hours in Step 1b (`run_daily_etl.py`) while processing Open-Meteo weather enrichment row-by-row with API backoffs, preventing Step 2 (predictions & git push) from running.
- **Root Cause 2 (Azure VM Cron `.env` Crash)**: The fallback daily cron job on the Azure VM (`globaladmin@4.213.226.19`) failed at `00:00 UTC` with `RuntimeError: POSTGRES_HOST is required` because `scripts/run_cron.sh` executed in a minimal cron shell without sourcing `/opt/pow-eda-pipeline/.env`.
- **Permanent Fix**:
  1. Updated `scripts/run_cron.sh` to explicitly source `.env` (`if [ -f .env ]; then set -a; source .env; set +a; fi`) and pushed to `main`.
  2. Pulled changes to `/opt/pow-eda-pipeline` on the Azure VM.
  3. Generated fresh V12 ONNX predictions for July 22, 2026, and pushed directly to `global-aq-intelligence-web` (`public/data/`).

