#!/bin/bash
# Global AQ Intelligence — Daily Pipeline Runner
# Order matters: everything the public site needs runs FIRST, then the
# slow historical backfill uses whatever time is left.
#
#   1. incremental collect (recent days only)   ~15 min
#   2. ETL                                      ~10 min
#   3. inference                                ~1 min
#   4. validate -> accuracy.json                ~1 min
#   5. publish to frontend (commit + push)      ~1 min
#   6. backfill (time-boxed, resumable)         <= BACKFILL_MAX_MIN
#
# Scheduled via: crontab -e → 0 18 * * * /path/to/run_cron_local.sh
#
# NOTE: No `set -e` — individual steps use `|| echo WARNING` guards
# so one failure doesn't halt the whole pipeline.

PIPELINE_DIR="/Users/divyanshailani/Desktop/pow-eda-pipeline"
FRONTEND_DIR="/Users/divyanshailani/Desktop/global-aq-intelligence"
LOCK_FILE="/tmp/global_aqi_pipeline.lock"

# Backfill is resumable via logs/backfill_state.json, so cutting it off
# mid-chunk is safe — the next run picks up where this one stopped.
BACKFILL_MAX_MIN=90

# Logging is owned by the caller (crontab redirects to logs/cron.log).
# Previously this line did `exec >> >(tee -a "$LOG_FILE") 2>&1`, which both
# forked a second bash for the process substitution AND wrote every line
# twice when the caller was already redirecting to the same file.
# When run interactively, redirect yourself:  ./run_cron_local.sh >> logs/cron.log 2>&1

# ── Run lock: never let two pipeline runs overlap on the same tables ──
if [ -d "$LOCK_FILE" ]; then
    if [ -n "$(find "$LOCK_FILE" -maxdepth 0 -mmin +360 2>/dev/null)" ]; then
        echo "WARNING: clearing stale lock (>6h old)"
        rmdir "$LOCK_FILE" 2>/dev/null
    else
        echo "SKIP: another pipeline run is in progress ($(date)). Exiting."
        exit 0
    fi
fi
mkdir "$LOCK_FILE" 2>/dev/null || { echo "SKIP: could not acquire lock."; exit 0; }
trap 'rmdir "$LOCK_FILE" 2>/dev/null' EXIT

echo "==========================================="
echo "Started daily pipeline at $(date)"

cd "$PIPELINE_DIR" || exit 1

git pull origin main || echo "WARNING: git pull failed, continuing with local version"

if [ -f "$PIPELINE_DIR/.env" ]; then
    set -a
    # shellcheck disable=SC1091
    source "$PIPELINE_DIR/.env"
    set +a
fi

# shellcheck disable=SC1091
source "$PIPELINE_DIR/venv/bin/activate"

# ── Step 1: Incremental fetch only (recent days). Backfill deferred to the end.
echo "Fetching OpenAQ data (incremental)..."
python3 "$PIPELINE_DIR/scripts/run_daily_collector.py" --incremental-only \
    || echo "WARNING: collector returned non-zero"

# ── Step 2: ETL — clean raw → clean_measurements, features → daily_features
# Retried: Azure PG drops long transactions when the home IP rotates mid-run.
# The insert is ON CONFLICT DO NOTHING, so re-running is safe/idempotent.
echo "Running ETL..."
for attempt in 1 2 3; do
    if python3 "$PIPELINE_DIR/scripts/run_daily_etl.py" --recent-days 5 --max-enrich 300; then
        echo "ETL succeeded on attempt $attempt"
        break
    fi
    echo "WARNING: ETL attempt $attempt failed"
    [ "$attempt" -lt 3 ] && sleep 60
done

# ── Step 3: V12 ONNX inference from acc 2 daily_features
echo "Running ONNX inference..."
python3 "$PIPELINE_DIR/scripts/predict_v12_onnx.py" || echo "WARNING: inference returned non-zero"

# ── Step 4: Validate matured forecasts → refresh accuracy.json
echo "Validating past forecasts..."
python3 "$PIPELINE_DIR/scripts/validate_predictions.py" || echo "WARNING: validation returned non-zero"

# ── Step 5: Sync predictions to frontend public/data/
echo "Syncing to frontend..."
mkdir -p "$FRONTEND_DIR/public/data"
for f in "$PIPELINE_DIR/site_data/"*.json; do
    [ -f "$f" ] && cp "$f" "$FRONTEND_DIR/public/data/"
done

# ── Step 6: Commit & push frontend so the live site actually updates.
echo "Publishing frontend..."
cd "$FRONTEND_DIR" || exit 1
# Stage FIRST: `git pull --rebase` refuses to run with unstaged changes, and
# the sync step above always dirties public/data/. Staging then rebasing keeps
# our commit on top of any remote work without a merge conflict.
git add public/data/*.json
if git diff --cached --quiet; then
    echo "No prediction changes to publish."
else
    git commit -m "auto: daily V12 predictions $(date +%Y-%m-%d)" || echo "WARNING: commit failed"
    git pull --rebase origin main || echo "WARNING: frontend rebase failed"
    git push origin main \
        && echo "Frontend published." \
        || echo "WARNING: frontend push failed"
fi
cd "$PIPELINE_DIR" || exit 1

echo "PUBLIC-FACING PIPELINE COMPLETE at $(date)"

# ── Step 7: Historical backfill, time-boxed so it can never delay the above.
# Progress is checkpointed in logs/backfill_state.json; a timeout just means
# tomorrow's run continues from the same point.
echo "Starting backfill (max ${BACKFILL_MAX_MIN}m)..."
if command -v timeout >/dev/null 2>&1; then
    TIMEOUT_BIN=timeout
elif command -v gtimeout >/dev/null 2>&1; then
    TIMEOUT_BIN=gtimeout
else
    TIMEOUT_BIN=""
fi

if [ -n "$TIMEOUT_BIN" ]; then
    $TIMEOUT_BIN "${BACKFILL_MAX_MIN}m" \
        python3 "$PIPELINE_DIR/scripts/run_daily_collector.py" --backfill-only
    rc=$?
    if [ $rc -eq 124 ]; then
        echo "Backfill hit ${BACKFILL_MAX_MIN}m limit — will resume next run."
    elif [ $rc -ne 0 ]; then
        echo "WARNING: backfill returned $rc"
    else
        echo "Backfill chunk complete."
    fi
else
    # No timeout binary: run in background and kill after the budget.
    python3 "$PIPELINE_DIR/scripts/run_daily_collector.py" --backfill-only &
    bf_pid=$!
    ( sleep $((BACKFILL_MAX_MIN * 60)); kill "$bf_pid" 2>/dev/null ) &
    watchdog=$!
    wait "$bf_pid" 2>/dev/null
    kill "$watchdog" 2>/dev/null
    echo "Backfill finished or hit ${BACKFILL_MAX_MIN}m limit."
fi

echo "Finished daily pipeline at $(date)"
