#!/bin/bash
# Global AQ Intelligence — Daily Pipeline Runner
# Runs: fetch → ETL → inference → sync to frontend
# Scheduled via: crontab -e → 0 0 * * * /path/to/run_cron_local.sh
# Or via launchd auto_collect.py → runs this script end-to-end

# NOTE: No `set -e` — individual steps use `|| echo WARNING` guards
# so one failure doesn't halt the whole pipeline.

LOG_FILE="/Users/divyanshailani/Desktop/pow-eda-pipeline/logs/cron.log"
PIPELINE_DIR="/Users/divyanshailani/Desktop/pow-eda-pipeline"
FRONTEND_DIR="/Users/divyanshailani/Desktop/global-aq-intelligence"

exec >> >(tee -a "$LOG_FILE") 2>&1

echo "==========================================="
echo "Started daily pipeline at $(date)"

cd "$PIPELINE_DIR"

# Pull latest pipeline code (keep local scripts up to date)
git pull origin main || echo "WARNING: git pull failed, continuing with local version"

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
python3 "$PIPELINE_DIR/scripts/run_daily_collector.py" || echo "WARNING: collector returned non-zero"

# Step 2: ETL — clean raw → clean_measurements, features → daily_features
echo "Running ETL..."
python3 "$PIPELINE_DIR/scripts/run_daily_etl.py" --recent-days 5 || echo "WARNING: ETL returned non-zero"

# Step 3: V12 ONNX inference from acc 2 daily_features
echo "Running ONNX inference..."
python3 "$PIPELINE_DIR/scripts/predict_v12_onnx.py" || echo "WARNING: inference returned non-zero"

# Step 4: Sync predictions to frontend public/data/
echo "Syncing to frontend..."
mkdir -p "$FRONTEND_DIR/public/data"
for f in "$PIPELINE_DIR/site_data/"*.json; do
    [ -f "$f" ] && cp "$f" "$FRONTEND_DIR/public/data/" || true
done

echo "Finished daily pipeline at $(date)"