#!/bin/bash
set -euo pipefail
# Global AQ — backup of the tables that cannot be rebuilt.
#
# Usage: scripts/deployment/backup_db.sh [backup_dir]
#   default dir: <repo>/backups   (override with $BACKUP_DIR or argv[1])
#   retention:   4 copies per table, newest kept
#   env:         loaded from <repo>/.env
#
# What is deliberately NOT backed up: raw_measurements and clean_measurements
# (~24 GB together, 72% of the database). Both are re-derivable — the OpenAQ
# daily S3 archive is the source of truth and scripts/bulk_backfill_local.py
# rebuilds them — so dumping them would cost hours, fill the disk, and protect
# nothing that is not already recoverable. daily_features, the engineered
# output the models actually consume, IS backed up.
#
# Restore:  pg_restore -h $POSTGRES_HOST -U $POSTGRES_USER -d $POSTGRES_DB \
#             --data-only --disable-triggers backups/<table>_<stamp>.dump

REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
# cron supplies a minimal PATH and pg_dump lives in the EDB PostgreSQL install,
# so resolve the client tools explicitly rather than depending on the caller.
export PATH="/Library/PostgreSQL/18/bin:/opt/homebrew/bin:/usr/local/bin:$PATH"
BACKUP_DIR="${1:-${BACKUP_DIR:-$REPO_ROOT/backups}}"
DATE=$(date +%Y%m%d_%H%M%S)
RETENTION=4
TABLES=(stations daily_features prediction_log validation_ledger pipeline_runs)

if [ -f "$REPO_ROOT/.env" ]; then
    set -a
    # shellcheck disable=SC1091
    source "$REPO_ROOT/.env"
    set +a
fi

for var in POSTGRES_HOST POSTGRES_USER POSTGRES_PASSWORD POSTGRES_DB; do
    if [ -z "${!var:-}" ]; then
        echo "ERROR: $var is not set (expected in $REPO_ROOT/.env)" >&2
        exit 1
    fi
done

mkdir -p "$BACKUP_DIR"
echo "=== backup $(date -u +%FT%TZ) -> $BACKUP_DIR (host: $POSTGRES_HOST) ==="

for TABLE in "${TABLES[@]}"; do
    OUT="$BACKUP_DIR/${TABLE}_${DATE}.dump"
    # Custom format: compressed and restorable in parallel, unlike the
    # --column-inserts text dumps this script used to produce.
    if PGPASSWORD="$POSTGRES_PASSWORD" pg_dump \
            -h "$POSTGRES_HOST" -p "${POSTGRES_PORT:-5432}" \
            -U "$POSTGRES_USER" -d "$POSTGRES_DB" \
            --format=custom --data-only --table="$TABLE" \
            --file="$OUT"; then
        # A dump that cannot be listed is not a backup.
        objs=$(PGPASSWORD="$POSTGRES_PASSWORD" pg_restore --list "$OUT" 2>/dev/null | wc -l | tr -d ' ')
        echo "  ok   $TABLE -> $(basename "$OUT") ($(du -h "$OUT" | cut -f1), $objs objects)"
    else
        echo "  FAIL $TABLE (pg_dump exited non-zero)" >&2
        rm -f "$OUT"
        exit 1
    fi
done

# Rotate: newest first, delete everything past $RETENTION. No grep, no name parsing.
for TABLE in "${TABLES[@]}"; do
    ls -1t "$BACKUP_DIR/${TABLE}_"*.dump 2>/dev/null \
        | tail -n "+$((RETENTION + 1))" \
        | while read -r old; do
            echo "  pruning $(basename "$old")"
            rm -f "$old"
        done
done

echo "=== done $(date -u +%FT%TZ); ${#TABLES[@]} tables, keeping $RETENTION generations ==="
ls -lh "$BACKUP_DIR"
