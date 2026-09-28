#!/usr/bin/env python3
"""Two-stage retention for the hourly measurement tables, sized for the 32 GiB cap.

Why this shape (and not DELETE/VACUUM FULL):
    globalaqi-archive has storage auto-grow DISABLED, so the 32 GiB ceiling is
    hard. A rewrite of clean_measurements in place needs the old table (17 GB)
    plus the retained copy (~10 GB) at the same time - about 34 GB - which
    would fill the disk and take the pipeline down. TRUNCATE releases every
    page at once, so the order is:

        archive  : stream BOTH the rows to be pruned and the rows to be kept to
                   local gzip files (server-side COPY, no client buffering)
        reload   : TRUNCATE, then stream the kept file back in

    Peak in Azure is therefore the current size plus nothing.

Retention cutoff is the pipeline's own requirement: src/features.py reads
clean_measurements through a 90-day lookback, so older hourly rows are already
unusable by the ETL. They remain rebuildable from the OpenAQ daily S3 archive
(scripts/bulk_backfill_local.py) and a local copy is kept here as well.

Usage:
    python3 -m scripts.operations.prune_measurements archive --cutoff 2026-06-26
    python3 -m scripts.operations.prune_measurements reload  --cutoff 2026-06-26
    python3 -m scripts.operations.prune_measurements verify  --cutoff 2026-06-26
"""

import argparse
import gzip
import hashlib
import json
import os
import re
import sys
import time

import psycopg2

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, REPO)
from src.config import DB_CONFIG  # noqa: E402

ARCHIVE_DIR = os.path.join(REPO, "data", "archive")
STATE = os.path.join(ARCHIVE_DIR, "prune_state.json")

# Preserve ids: nothing references clean_measurements.id, but keeping them makes
# a reload byte-identical to the source and keeps the sequence meaningful.
COLUMNS = ("id, station_id, sensor_id, parameter, value, unit, datetime_utc, "
           "datetime_local, cleaning_flags, is_valid, cleaned_at")


def connect():
    conn = psycopg2.connect(**DB_CONFIG)
    conn.autocommit = True
    return conn


def _path(kind: str, cutoff: str) -> str:
    return os.path.join(ARCHIVE_DIR, f"clean_measurements_{kind}_{cutoff}.csv.gz")


def copy_out(conn, op: str, cutoff: str, path: str) -> int:
    """Server-side COPY to a local gzip file. Returns the row count.

    The cutoff is inlined, not bound: psycopg2's copy_expert does no parameter
    substitution, so a %s would reach the server literally. main() validates it
    as a date first.
    """
    sql = (f"COPY (SELECT {COLUMNS} FROM clean_measurements "
           f"WHERE datetime_utc {op} '{cutoff}') TO STDOUT WITH (FORMAT csv)")
    t0 = time.time()
    with gzip.open(path, "wb", compresslevel=6) as fh, conn.cursor() as cur:
        cur.copy_expert(sql, fh, size=1 << 20)
        rows = cur.rowcount
    size = os.path.getsize(path) / 1024 ** 3
    print(f"  wrote {path} ({size:.2f} GiB, {rows:,} rows, {time.time() - t0:.0f}s)", flush=True)
    return rows


def archive(cutoff: str) -> None:
    os.makedirs(ARCHIVE_DIR, exist_ok=True)
    conn = connect()
    with conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM clean_measurements WHERE datetime_utc < %s", (cutoff,))
        n_old = cur.fetchone()[0]
        cur.execute("SELECT count(*) FROM clean_measurements WHERE datetime_utc >= %s", (cutoff,))
        n_keep = cur.fetchone()[0]
        cur.execute("SELECT pg_size_pretty(pg_database_size(current_database()))")
        print(f"database before: {cur.fetchone()[0]}")
    print(f"rows to prune: {n_old:,} | rows to keep: {n_keep:,} (cutoff {cutoff})")

    old_rows = copy_out(conn, "<", cutoff, _path("pruned", cutoff))
    keep_rows = copy_out(conn, ">=", cutoff, _path("kept", cutoff))

    problems = []
    if old_rows != n_old:
        problems.append(f"pruned archive has {old_rows:,} rows, database reports {n_old:,}")
    if keep_rows != n_keep:
        problems.append(f"kept archive has {keep_rows:,} rows, database reports {n_keep:,}")
    if problems:
        print("ARCHIVE VERIFICATION FAILED - do not run reload:")
        for p in problems:
            print("  -", p)
        raise SystemExit(1)

    state = {
        "cutoff": cutoff,
        "archived_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "pruned_rows": old_rows,
        "kept_rows": keep_rows,
        "pruned_file": _path("pruned", cutoff),
        "kept_file": _path("kept", cutoff),
        "sha256": {
            "pruned": sha256(_path("pruned", cutoff)),
            "kept": sha256(_path("kept", cutoff)),
        },
    }
    with open(STATE, "w") as fh:
        json.dump(state, fh, indent=2)
    print(f"archive verified: {old_rows:,} pruned + {keep_rows:,} kept rows, state -> {STATE}")


def sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def reload_keep(cutoff: str) -> None:
    if not os.path.exists(STATE):
        raise SystemExit("no archive state - run the archive stage first")
    with open(STATE) as fh:
        state = json.load(fh)
    if state["cutoff"] != cutoff:
        raise SystemExit(f"archive is for cutoff {state['cutoff']}, refusing to reload for {cutoff}")
    kept = state["kept_file"]
    if sha256(kept) != state["sha256"]["kept"]:
        raise SystemExit("kept archive digest changed - refusing to reload")

    conn = connect()
    with conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM clean_measurements")
        before = cur.fetchone()[0]
        cur.execute("SELECT pg_size_pretty(pg_database_size(current_database()))")
        size_before = cur.fetchone()[0]
    print(f"reload: clean_measurements has {before:,} rows, database {size_before}")

    t0 = time.time()
    with conn.cursor() as cur:
        cur.execute("TRUNCATE clean_measurements")
    print(f"  truncated ({time.time() - t0:.0f}s) - all pages released", flush=True)

    t0 = time.time()
    with gzip.open(kept, "rt") as fh, conn.cursor() as cur:
        cur.copy_expert(
            f"COPY clean_measurements ({COLUMNS}) FROM STDIN WITH (FORMAT csv)", fh, size=1 << 20
        )
    print(f"  reloaded {state['kept_rows']:,} rows ({time.time() - t0:.0f}s)", flush=True)

    with conn.cursor() as cur:
        cur.execute("SELECT setval(pg_get_serial_sequence('clean_measurements','id'), "
                    "COALESCE((SELECT max(id) FROM clean_measurements), 1))")
        cur.execute("ANALYZE clean_measurements")
        cur.execute("VACUUM (ANALYZE) clean_measurements")
        cur.execute("SELECT count(*), pg_size_pretty(pg_database_size(current_database())) "
                    "FROM clean_measurements")
        rows, size = cur.fetchone()
    print(f"reload complete: {rows:,} rows, database {size}")
    if rows != state["kept_rows"]:
        raise SystemExit(f"row count mismatch after reload: {rows:,} != {state['kept_rows']:,}")


def verify(cutoff: str) -> None:
    conn = connect()
    with conn.cursor() as cur:
        cur.execute("SELECT count(*), min(datetime_utc)::date, max(datetime_utc)::date "
                    "FROM clean_measurements")
        rows, lo, hi = cur.fetchone()
        cur.execute("SELECT count(*) FROM clean_measurements WHERE datetime_utc < %s", (cutoff,))
        stale = cur.fetchone()[0]
        for t in ("clean_measurements", "raw_measurements", "daily_features"):
            cur.execute(f"SELECT pg_size_pretty(pg_total_relation_size('{t}'))")
            print(f"  {t:22} {cur.fetchone()[0]}")
        cur.execute("SELECT pg_size_pretty(pg_database_size(current_database())), "
                    "round(100*pg_database_size(current_database())::numeric/"
                    "(32*1024*1024*1024), 1)")
        size, pct = cur.fetchone()
        cur.execute("SELECT count(*) FROM daily_features")
        print(f"  daily_features rows    {cur.fetchone()[0]:,}")
    print(f"clean_measurements: {rows:,} rows, {lo} .. {hi}; rows older than {cutoff}: {stale:,}")
    print(f"database: {size} ({pct}% of the 32 GiB cap)")


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("stage", choices=("archive", "reload", "verify"))
    p.add_argument("--cutoff", required=True, help="YYYY-MM-DD; rows older than this are pruned")
    a = p.parse_args()
    # The cutoff is inlined into COPY statements (copy_expert does not bind
    # parameters), so it must be validated before it can reach SQL.
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", a.cutoff):
        raise SystemExit("--cutoff must be YYYY-MM-DD")
    {"archive": archive, "reload": reload_keep, "verify": verify}[a.stage](a.cutoff)


if __name__ == "__main__":
    main()
