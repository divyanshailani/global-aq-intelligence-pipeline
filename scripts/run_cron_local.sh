#!/bin/bash
set -e

# Global AQ Intelligence — Daily Pipeline Runner
# Runs: fetch → ETL → inference → sync to frontend (local dev server handles serving)
# Scheduled via: crontab -e → 0 0 * * * /path/to/run_cron_local.sh
#
# No auto-commit/push — frontend dev server serves from local public/data/
# Deploy to production: manual git push from global-aq-intelligence repo

LOG_FILE="/Users/divyanshailani/Desktop/pow-eda-pipeline/logs/cron.log"
PIPELINE_DIR="/Users/divyanshailani/Desktop/pow-eda-pipeline"
FRONTEND_DIR="/Users/divyanshailani/Desktop/global-aq-intelligence"

exec >> >(tee -a "$LOG_FILE") 2>&1

echo "==========================================="
echo "Started daily pipeline at $(date)"

cd "$PIPELINE_DIR"

# Pull latest pipeline code (keep local scripts up to date)
git pull origin main

# Load environment (POSTGRES_HOST points to acc 2: globalaqi-archive)
if [ -f "$PIPELINE_DIR/.env" ]; then
    set -a
    source "$PIPELINE_DIR/.env"
    set +a
fi

# Activate venv
source "$PIPELINE_DIR/venv/bin/activate"

# Step 1: Fetch new data from OpenAQ (last 7 days + continue backfill)
echo "Fetching OpenAQ data..."
python3 "$PIPELINE_DIR/scripts/run_daily_collector.py"

# Step 2: ETL — clean raw → clean_measurements, features → daily_features
echo "Running ETL..."
python3 "$PIPELINE_DIR/scripts/run_daily_etl.py"

# Step 3: V12 ONNX inference from acc 2 daily_features
echo "Running ONNX inference..."
python3 "$PIPELINE_DIR/scripts/predict_v12_onnx.py"

# Step 4: Sync predictions to frontend public/data/
echo "Syncing to frontend..."
mkdir -p "$FRONTEND_DIR/public/data"
cp "$PIPELINE_DIR/site_data/"*.json "$FRONTEND_DIR/public/data/" 2>/dev/null || true
cp "$PIPELINE_DIR/site_data/model_meta.json "$FRONTEND_DIR/public/data/" 2>/dev/null || true

echo "Finished daily pipeline at $(date)"