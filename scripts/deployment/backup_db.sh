#!/bin/bash
set -e
# Global AQ — Weekly Backup Script
# Usage: ./scripts/deployment/backup_db.sh
# Runs: weekly via cron (Sunday midnight), or manually
# Backed up tables: clean_measurements, daily_features, stations
# Retention: 4 weekly backups (rotate oldest)
# Backup location: ~/Desktop/pow-eda-pipeline/backups/

BACKUP_DIR="$HOME/Desktop/pow-eda-pipeline/backups"
DATE=$(date +%Y%m%d_%H%M%S)
RETENTION=4

# Load .env
PIPELINE_DIR="$HOME/Desktop/pow-eda-pipeline"
if [ -f "$PIPELINE_DIR/.env" ]; then
    set -a
    source "$PIPELINE_DIR/.env"
    set +a
fi

mkdir -p "$BACKUP_DIR"

echo "==========================================="
echo "Starting backup at $(date)"
echo "Host: $POSTGRES_HOST"

# Dump each table separately (clean_measurements is 7GB, splitting avoids OOM)
TABLES=("stations" "daily_features" "clean_measurements")

for TABLE in "${TABLES[@]}"; do
    OUT="$BACKUP_DIR/${TABLE}_${DATE}.sql.gz"
    echo "Dumping $TABLE..."
    PGPASSWORD="$POSTGRES_PASSWORD" pg_dump \
        -h "$POSTGRES_HOST" \
        -p 5432 \
        -U "$POSTGRES_USER" \
        -d "$POSTGRES_DB" \
        --data-only \
        --column-inserts \
        -t "$TABLE" \
    | gzip > "$OUT"
    SIZE=$(du -h "$OUT" | cut -f1)
    echo "  → $OUT ($SIZE)"
done

# Rotate: keep only last $RETENTION backups per table
for TABLE in "${TABLES[@]}"; do
    cd "$BACKUP_DIR"
    # List matching files sorted by name (oldest first), keep last $RETENTION
    KEEP=$(ls -1 "${TABLE}_"*".sql.gz" 2>/dev/null | tail -n "$RETENTION")
    for f in "${TABLE}_"*".sql.gz"; do
        if ! echo "$KEEP" | grep -q "$(basename "$f")"; then
            echo "Removing old backup: $f"
            rm -f "$f"
        fi
    done
done

echo "Backup complete at $(date)"
echo "Files in $BACKUP_DIR:"
ls -lh "$BACKUP_DIR"