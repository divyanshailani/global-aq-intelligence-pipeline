# Script Inventory

This file answers one question: which scripts are part of the current system, and which are historical or manual?

Classification is based on repository evidence checked on 2026-08-06:

- GitHub Actions workflow commands
- local scheduler and shell entrypoints
- Docker/systemd entrypoints
- imports and subprocess callers
- tests and documentation
- directory placement and model-generation names

A script being marked `MANUAL` does not mean it is useless. It means it is not automatically called by the current daily public-data publisher.

## 1. Current Scheduled Production

These are the only Python stages called directly by `.github/workflows/daily_pipeline.yml`:

| Path | Role | Evidence |
| --- | --- | --- |
| `scripts/pipeline/__init__.py`, `scripts/operations/__init__.py`, `scripts/models/__init__.py`, `scripts/evaluation/__init__.py`, `scripts/deployment/__init__.py` | ACTIVE PACKAGE MARKERS | Keep categorized script directories importable as packages. |
| `scripts/pipeline/run_daily_collector.py` | Incremental OpenAQ collection; also isolated historical backfill mode | Called by both daily and historical-backfill workflows |
| `scripts/pipeline/run_daily_etl.py` | Cleaning, feature generation, weather/AOD enrichment | Called by daily workflow |
| `scripts/pipeline/predict_v12_onnx.py` | V12 ONNX inference and site-data generation | Called by daily workflow |
| `scripts/pipeline/validate_predictions.py` | Prediction contract and accuracy validation | Called by daily workflow |
| `scripts/pipeline/assert_data_contract.py` | Post-ETL write contract (NULL country_code, freshness, NULL budgets) — blocks publish | Called by daily workflow (added 2026-09-28) |
| `scripts/pipeline/assert_site_data_fresh.py` | Blocks publish when observation lag exceeds budget (generated_at is always "now") | Called by daily workflow |

The production order is:

```text
run_daily_collector.py
  -> run_daily_etl.py
  -> assert_data_contract.py        (blocking: written rows obey contract)
  -> predict_v12_onnx.py
  -> validate_predictions.py
  -> assert_site_data_fresh.py      (blocking: observations advanced, not just generated_at)
  -> frontend publication
```

## 2. Current Production Dependencies

These are not top-level workflow stages, but moving or deleting them can break the scheduled path:

| Path | Role | Evidence |
| --- | --- | --- |
| `scripts/pipeline/fetch_openaq.py` | Primary S3/OpenAQ collection implementation | Imported by `run_daily_collector.py` |
| `scripts/pipeline/legacy_api_fetcher.py` | Live OpenAQ API fallback | Dynamically imported by `run_daily_collector.py` when S3 returns no rows or fails |
| `scripts/pipeline/fetch_daily_weather.py` | Daily weather fetch helper | Imported by `run_daily_etl.py` |
| `scripts/pipeline/fetch_daily_aod.py` | Daily AOD fetch helper | Imported by `run_daily_etl.py` |
| `src/config.py` | Environment, database, model, and output paths | Imported throughout active stages |
| `src/cleaning.py` | Raw-to-clean measurement processing | Imported by `run_daily_etl.py` |
| `src/features.py` | Feature and weather/AOD feature generation | Imported by `run_daily_etl.py` and inference-related code |
| `src/api_fallback_manager.py` | Bounded API retry/fallback behavior | Imported by ETL weather/AOD helpers |
| `src/aggregations.py` | Aggregation library code | Used by source processing paths; keep until import audit proves otherwise |
| `src/process_aq.py` | Processing library code | Covered by maintained processing tests |

## 3. Current Operational Entry Points

These are active outside GitHub Actions and must be treated separately from the scheduled publisher:

| Path | Status | Meaning |
| --- | --- | --- |
| `scripts/deployment/run_cron_local.sh` | ACTIVE LOCAL SCHEDULER | Runs collection, ETL, V12 inference, validation, frontend publication, and time-boxed backfill on the Mac. It is a second publisher path, not a dead script. |
| `scripts/deployment/admin_dashboard.py` | ACTIVE ADMIN/API ENTRYPOINT | Dockerfile and `scripts/deployment/setup_vm.sh` launch `uvicorn scripts.deployment.admin_dashboard:app`. Its controls invoke maintained V12 pipeline stages. |
| `scripts/deployment/setup_vm.sh` | DEPLOYMENT TOOL | Creates the systemd service for `admin_dashboard.py`; run only during VM provisioning or deliberate reconfiguration. |
| `Dockerfile` / `docker-compose.yml` | ACTIVE DEPLOYMENT DEFINITION | The container entrypoint is the active admin dashboard module. |
| `scripts/deployment/run_cron.sh` | LEGACY VM SCHEDULER CANDIDATE | It still runs V12 inference, but its flow differs from the current GitHub Actions workflow and lacks the current collection/ETL contract. Do not use it without confirming the VM's actual scheduler first. |

The retired alternate API is preserved under `scripts/archive/historical/api/` and is outside the production import path.

## 4. Maintained Tests and Verification

These are test or verification utilities, not production pipeline stages:

| Path | Status |
| --- | --- |
| `tests/test_codex_fixes.py` | MAINTAINED pytest suite |
| `tests/test_processing.py` | MAINTAINED pytest suite |
| `tests/verify_gap_cap.py` | Manual structural/runtime verification |
| `tests/verify_insert_counts.py` | Manual/database verification; may require live DB |
| `tests/verify_pipeline_ordering.py` | Manual workflow verification |
| `tests/verify_pipeline_structure.py` | Manual structural verification |
| `tests/verify_validator_livedb.py` | Manual/live database verification |
| `tests/run_all.py` | Manual test aggregator |

Pytest is intentionally restricted to `tests/` by `pytest.ini`. The verification scripts under `tests/` are not automatically executed by `pytest` unless they match the configured test naming rules.

## 5. Manual Backfill and Data Operations

These are purposeful operational tools, but no current scheduled workflow calls them directly:

```text
scripts/operations/backfill_aod_partitioned.py
scripts/operations/backfill_full_aod.py
scripts/operations/backfill_full_weather.py
scripts/operations/backfill_recent_aod.py
scripts/operations/backfill_recent_weather.py
scripts/operations/backfill_weather_batch.py
scripts/operations/bulk_backfill_local.py
scripts/operations/export_azure_to_parquet.py
scripts/operations/fetch_defra_bulk.py
scripts/operations/fetch_epa_bulk.py
scripts/operations/fetch_firms_fire.py
scripts/operations/fetch_nasa_global.py
scripts/operations/fetch_nasa_power_extra.py
scripts/operations/fetch_nsw_bulk.py
scripts/operations/fetch_openaq_india.py
scripts/operations/fetch_visual_crossing.py
scripts/operations/fetch_weather.py
scripts/operations/ingest_openaq.py
scripts/operations/ingest_openaq_data.py
scripts/operations/merge_nasa_fire.py
scripts/operations/patch_weather_batch.py
scripts/operations/patch_weather_standalone.py
scripts/operations/process_firms_fire.py
scripts/operations/process_firms_global.py
scripts/operations/swarm_weather_fetch.py
scripts/operations/update_db_nasa_weather.py
scripts/operations/build_global_features.py
scripts/operations/prune_measurements.py
scripts/operations/aod_fill_loop.sh
```

These can write to databases or regenerate data. They should remain in place until each one has an owner, command contract, and rollback note.

## 6. Historical Model Training, Evaluation, and Experimentation

Only these remain in the active tree (they are NOT part of the daily inference workflow, but they are maintained and runnable):

```text
scripts/evaluation/live_validation.py   # live-accuracy scoring used by the drift check
src/evaluate_v12_pure.py                # the honest evaluation engine (Nuclear Drop + t+h + MASE)
src/v12_tuning.py                       # Optuna/XGBoost training used for V12 retrains
```

Everything else in this category — `train_v5..v11`, `tune_v11_per_country`, `optimize_h1_optuna`, `convert_models_to_onnx`, `retrain_pipeline`, the v9/v11 plots and per-horizon probe tests — is archived and excluded from production imports:

```text
scripts/archive/research/models/     (13 training/tuning scripts)
scripts/archive/research/evaluation/ (evaluation + probe scripts)
scripts/archive/research/plots/      scripts/archive/research/notebooks/
scripts/archive/legacy/              (predict_pipeline.py, retrain_pipeline.py)
```

`find scripts/archive -name '*.py'` gives the full list. This file stopped enumerating archived scripts individually on 2026-09-28, after an audit found nearly a third of the paths previously listed here had silently moved to archive locations — an inventory that lists files which do not exist trains readers to distrust every line in it.

**Deleted 2026-09-28 — `evaluate_v11_vs_v12.py`** (was `scripts/evaluation/`, last seen in `scripts/archive/research/evaluation/`). Its V11 predictions target `date + h` but were scored against `date`'s same-day PM2.5, while V12 was scored through correct `target_date` remapping — every "V12 wins N/N" figure it produced is an artifact of the comparison, not of the models. Removed rather than archived so nobody re-runs it and re-quotes the number; `git log --diff-filter=D -- '*evaluate_v11_vs_v12.py'` recovers it if the methodology review is ever needed. Honest V12 numbers come from `src/evaluate_v12_pure.py` (Nuclear Drop isolation, strict `t+h` alignment, MASE vs persistence). V11 cross-validation metrics must never be quoted beside those.

## 7. Legacy End-to-End Path

```text
scripts/archive/legacy/predict_pipeline.py
scripts/archive/legacy/retrain_pipeline.py
scripts/deployment/admin_dashboard.py -> predict_pipeline.py (manual admin action)
```

`predict_pipeline.py` is not used by either current GitHub Actions workflow and is not the V12 scheduled inference stage. It remains reachable through the admin dashboard and historical scripts, so it must not be deleted casually. Treat it as `LEGACY MANUAL`, not `CURRENT PRODUCTION`.

## 8. Archived and Diagnostic Utilities

| Path | Status |
| --- | --- |
| `scripts/archive/historical/fetchers/` | ARCHIVED historical fetch/merge implementations |
| `scripts/archive/` | ARCHIVED historical, research, manual, and legacy material; excluded from production imports |
| `scripts/deployment/` | ACTIVE deployment and admin entrypoints |
| `scripts/operations/check_db_health.py`, `scripts/operations/check_db_quick.py` | MANUAL database checks; watchdog composes their signals (see RUNBOOK.md) |
| `scripts/operations/pipeline_watchdog.py` + `scripts/deployment/com.globalaqi.watchdog.plist` + `scripts/deployment/install_watchdog.sh` | AUTONOMOUS external watchdog (launchd, see RUNBOOK.md) |
| `query_db.py`, `test_query.py` | MANUAL local database probes; ignored/untracked state |

Additional tracked legacy and diagnostic scripts covered by the same classifications:

```text
scripts/archive/historical/fetchers/fetch_open_meteo_aod.py
scripts/archive/historical/fetchers/fetch_openmeteo_all.py
scripts/archive/historical/fetchers/fetch_openmeteo_gb.py
scripts/archive/historical/fetchers/merge_openmeteo_all.py
scripts/archive/historical/fetchers/merge_openmeteo_gb.py
scripts/deployment/auto_commit.sh
scripts/deployment/backup_db.sh
scripts/archive/manual/diagnostics/check_issues.py
scripts/archive/manual/diagnostics/check_trials.py
scripts/archive/manual/diagnostics/fast_etl.py
scripts/archive/manual/diagnostics/rewrite_pipeline.py
scripts/deployment/migrate_db_to_azure.sh
```

The historical fetchers and manual diagnostics are archived outside the active source tree. Deployment shell entries remain active only where their scheduler/deployment callers are documented.

## 10. Scheduler Ownership Matrix

| Publisher/path | Host | Output target | Status |
| --- | --- | --- | --- |
| `.github/workflows/daily_pipeline.yml` | GitHub-hosted runner | Pipeline `site_data/` to frontend `public/data/` | AUTHORITATIVE HOSTED DAILY PUBLISHER |
| `scripts/deployment/run_cron_local.sh` | Local Mac | Pipeline `site_data/` to sibling frontend `public/data/` | ACTIVE LOCAL ALTERNATE; do not overlap |
| `scripts/deployment/run_cron.sh` | Legacy VM candidate | VM checkout `site_data/` to frontend checkout | LEGACY CANDIDATE; verify scheduler before use/retirement |
| `scripts/deployment/admin_dashboard.py` | Docker/systemd VM service | Manual legacy prediction path; API/admin operations | ACTIVE ADMIN ENTRYPOINT |
| `scripts/operations/pipeline_watchdog.py` (launchd `com.globalaqi.watchdog`) | Local Mac | GitHub issue + manual workflow dispatch | AUTONOMOUS EXTERNAL WATCHDOG; the only check that fires when the hosted run never starts |

The cleanup does not disable any publisher. Before changing scheduler state, record the host, scheduler definition, lock path, log path, last run, and replacement owner.

## 11. Remaining Support and Archive Files

These files are covered by the categories above but are listed explicitly so the inventory remains exhaustive:

| Path | Status |
| --- | --- |
| `api/__init__.py`, `scripts/__init__.py`, `src/__init__.py` | PACKAGE MARKERS; required for imports/package behavior |
| `src/evaluation.py` | ACTIVE SUPPORT LIBRARY for evaluation scripts |
| `tests/test_repository_contract.py` | MAINTAINED STATIC REGRESSION GUARDS for repository and publication contracts |
| `scripts/deployment/auto_collect.py` | MANUAL/LEGACY scheduler wrapper; current verification ensures it does not duplicate the collector |
| `scripts/operations/cleanup_prediction_log.py` | MANUAL DATA MAINTENANCE utility |
| `scripts/archive/manual/diagnostics/check_db.py`, `check_issues.py`, `check_trials.py`, `fast_etl.py`, `rewrite_pipeline.py` | MANUAL/HISTORICAL diagnostics preserved outside production |
| `scripts/archive/historical/fetchers/fetch_nasa_power.py`, `fetch_open_meteo_aod.py`, `fetch_openmeteo_all.py`, `fetch_openmeteo_gb.py`, `merge_openmeteo_all.py`, `merge_openmeteo_gb.py` | ARCHIVED historical fetch/merge implementations; do not use for daily production |
| `scripts/operations/check_db_health.py`, `check_db_quick.py` | MANUAL database checks (see section 8) |

The root-level `check_db_health2.py` / `_comprehensive.py` / `_fast.py` variants listed by older inventories are gone from disk; `scripts/operations/check_db_health.py` is the single entry point.

## 12. Safe Rules

- Only the four scripts in section 1 are the scheduled daily public pipeline.
- `legacy_api_fetcher.py` is a production fallback despite its name.
- `run_cron_local.sh` is active local automation and must be treated as a second publisher.
- `admin_dashboard.py` is an active API entrypoint, but its manual prediction action uses the legacy path.
- Do not delete files merely because they are not in the daily workflow.
- Before archiving a manual script, search for imports, subprocess calls, shell calls, workflow references, and deployment references.
- Before moving a production dependency, run the maintained tests and a dry structural verification.
- Never run database-writing or deployment scripts during a read-only inventory.

- `scripts/pipeline/repair_recent_weather.py`: bounded recent forecast/AOD auto-repair before the written-data contract; no archive calls, finite cells preserved.
- `tests/test_recent_weather_repair.py`: safety regressions for recent repair routing, bounds, and streamed clean-feature reads.
