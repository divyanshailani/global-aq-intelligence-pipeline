# Project Map

This repository has one production purpose: predict AQI and publish the site's forecast data.

## Production Flow

```text
OpenAQ / fallback APIs
        |
        v
scripts/run_daily_collector.py --incremental-only
        |
        v
scripts/run_daily_etl.py --recent-days 5 --max-enrich 300
        |  cleaning, features, weather and AOD enrichment
        v
scripts/predict_v12_onnx.py
        |  16 ONNX models: 4 countries x 4 horizons
        v
scripts/validate_predictions.py
        |
        v
 site_data/*.json -> global-aq-intelligence/public/data/*.json
```

There are four distinct runtime paths:

1. `.github/workflows/daily_pipeline.yml` is the current scheduled public-data publisher.
2. `.github/workflows/historical_backfill.yml` reuses only the collector in `--backfill-only` mode.
3. `scripts/run_cron_local.sh` is a separate local Mac scheduler that also publishes frontend data.
4. `scripts/run_cron.sh` is a legacy VM scheduler candidate that must be checked against the VM's actual scheduler before use or retirement. `scripts/admin_dashboard.py` is the Docker/systemd admin API and can manually invoke the legacy prediction runner.

The exact script classification is maintained in `SCRIPT_INVENTORY.md`. Do not infer that every script under `scripts/` is production or that every non-workflow script is dead.

### Publisher ownership

GitHub Actions is the authoritative hosted daily publisher for the public frontend contract. The local Mac and legacy VM paths are alternate publishers and must not run concurrently with each other or with a hosted run when they write the same database/output contract. No scheduler is disabled by this cleanup.

## Directory Ownership

| Path | Role | Status |
| --- | --- | --- |
| `.github/workflows/` | Scheduled production and isolated backfill workflows | Active |
| `scripts/` | Operational collectors, ETL, inference, validation, and data utilities | Mixed: active and historical tools |
| `scripts/diagnostics/` | Manual database checks and one-off pipeline utilities | Manual / historical |
| `src/` | Reusable cleaning, feature, aggregation, configuration, and evaluation code | Active library code |
| `models/v12/` | Current ONNX production model grid | Active, do not delete |
| `models/v5/`, `models/v6/`, `models/v9/`, `models/v9_4/`, `models/v11/` | Previous model generations and metadata | Historical reference |
| `site_data/` | Authoritative JSON staging output copied to the frontend repository | Active output, do not delete |
| `data/site_data/` | Older tracked JSON output location retained as historical evidence | Compatibility/reference; do not use as a new source |
| `data/raw/`, `data/processed/` | Local/regenerable datasets | Local or generated |
| `data/predictions_v12/` | Evaluation CSVs for model analysis | Historical evaluation artifacts |
| `sql/` | Database schema and migrations | Active database definition |
| `tests/` | Maintained pytest suite and structural verification | Active tests |
| `notebooks/`, `plots/`, `reports/` | Exploratory analysis and presentation artifacts | Historical/reference |
| `old_scripts/` | Retired data-fetching and merge scripts | Historical; do not use for daily production |
| `backups/`, `logs/`, `scratch/`, `venv/` | Local backup, runtime, scratch, and environment files | Local-only; never commit |
| `.agents/` | Local graph-memory database and generated graph views | Local-only; never commit |

## What Is Safe To Change

- Add or improve documentation and tests.
- Add new files under the appropriate directory.
- Move an unreferenced manual utility while preserving it in Git history.
- Remove a file only after checking Git references, workflow references, imports, and whether it is a current output or rollback artifact.

## What Requires Extra Care

- Do not move or rename production workflow scripts without updating every caller and validating a full dry run.
- Do not delete `models/v12/`, `site_data/`, `data/site_data/`, schema files, backups, or diagnostic artifacts just because they are large or old.
- Do not skip the weather/AOD enrichment stage: V12 inference requires `om_temperature`, `om_wind_speed`, `om_precipitation`, `om_aerosol_optical_depth`, `rolling_3day_precip`, and `aod_volatility_index`.
- Keep local secrets and credentials outside Git. `.env`, `CREDENTIALS.md`, and `.agents/` are intentionally ignored.

## Local Commands

```bash
python3 -m pytest -q
python3 -m compileall -q scripts src tests
python3 scripts/run_daily_collector.py --incremental-only
python3 scripts/run_daily_etl.py --recent-days 5 --max-enrich 300
python3 scripts/predict_v12_onnx.py
python3 scripts/validate_predictions.py
```

The final four commands perform real data work. Run them only when an operational pipeline run is explicitly intended.
