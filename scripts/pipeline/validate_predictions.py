#!/usr/bin/env python3
"""
Country-level forecast validation → accuracy.json

Why this exists:
  predict_v12_onnx.py logs COUNTRY-AGGREGATED forecasts (station_id IS NULL,
  predicted_value = mean of per-station predictions). The validator inside
  predict_pipeline.py matches on station_id, so it can never match these rows
  and live accuracy stayed null forever.

This script validates on the same axis the prediction was made on: country+date.

Usage:
    python scripts/pipeline/validate_predictions.py            # validate + write accuracy.json
    python scripts/pipeline/validate_predictions.py --dry-run  # report only, no writes
"""

import argparse
import json
import os
import sys
from datetime import date, datetime

import numpy as np
import psycopg2

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
from src.config import DB_CONFIG, COUNTRIES  # noqa: E402

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SITE_DATA_DIR = os.path.join(BASE_DIR, "site_data")

# Don't publish a live number off a handful of samples.
MIN_LIVE_VALIDATIONS = 30


def validate(conn, dry_run=False):
    """Fill actual_value/error for country forecasts whose target_date has passed."""
    today = date.today()

    with conn.cursor() as cur:
        # target_date >= run_date excludes stale-anchor rows (a run forecasting
        # into its own past), which are not real forward predictions.
        cur.execute(
            """
            SELECT id, country_code, target_date, predicted_value
            FROM prediction_log
            WHERE actual_value IS NULL
              AND station_id IS NULL
              AND target_date < %s
              AND target_date >= run_date
            ORDER BY target_date
            """,
            (today,),
        )
        pending = cur.fetchall()

        if not pending:
            print("  No country forecasts ready to validate.")
        else:
            print(f"  Found {len(pending)} country forecasts to validate...")

        validated = 0
        for pid, cc, target_date, predicted in pending:
            # Actual = same aggregation the forecast used: mean pm25 across
            # that country's reporting stations on the target date.
            cur.execute(
                """
                SELECT AVG(value)
                FROM daily_features
                WHERE country_code = %s
                  AND parameter = 'pm25'
                  AND date = %s
                  AND value IS NOT NULL
                """,
                (cc, target_date),
            )
            row = cur.fetchone()
            actual = float(row[0]) if row and row[0] is not None else None

            if actual is None:
                continue

            error = actual - predicted
            if not dry_run:
                cur.execute(
                    """
                    UPDATE prediction_log
                    SET actual_value = %s, error = %s, validated_at = NOW()
                    WHERE id = %s
                    """,
                    (actual, error, pid),
                )
            validated += 1

        if not dry_run:
            conn.commit()
        print(f"  Validated: {validated}/{len(pending)}")

        # ── Live metrics from everything validated in the last 90 days ──
        cur.execute(
            """
            SELECT country_code, actual_value, predicted_value
            FROM prediction_log
            WHERE actual_value IS NOT NULL
              AND station_id IS NULL
              AND target_date >= run_date
              AND validated_at >= NOW() - INTERVAL '90 days'
            """
        )
        rows = cur.fetchall()

    return validated, rows


def summarize(rows):
    """Overall + per-country MAE / accuracy from validated rows."""
    if not rows:
        return None, None, 0, {}

    actuals = np.array([r[1] for r in rows], dtype=float)
    preds = np.array([r[2] for r in rows], dtype=float)

    mae = float(np.mean(np.abs(actuals - preds)))
    mean_y = float(np.mean(actuals))
    acc = max(0.0, (1.0 - (mae / mean_y)) * 100.0) if mean_y > 0 else 0.0

    per_country = {}
    for cc in COUNTRIES:
        sub = [(a, p) for c, a, p in rows if c and c.strip() == cc]
        if not sub:
            continue
        a = np.array([s[0] for s in sub], dtype=float)
        p = np.array([s[1] for s in sub], dtype=float)
        c_mae = float(np.mean(np.abs(a - p)))
        c_mean = float(np.mean(a))
        per_country[cc] = {
            "mae": round(c_mae, 2),
            "accuracy_percentage": round(max(0.0, (1.0 - (c_mae / c_mean)) * 100.0), 2)
            if c_mean > 0
            else None,
            "sample_count": len(sub),
        }

    return mae, acc, len(rows), per_country


def main():
    ap = argparse.ArgumentParser(description="Validate country forecasts, write accuracy.json")
    ap.add_argument("--dry-run", action="store_true", help="report only, write nothing")
    args = ap.parse_args()

    print("=" * 50)
    print("Forecast Validation (country-level)")
    print("=" * 50)

    conn = psycopg2.connect(**DB_CONFIG)
    try:
        validated, rows = validate(conn, dry_run=args.dry_run)
    finally:
        conn.close()

    mae, acc, n, per_country = summarize(rows)

    if n >= MIN_LIVE_VALIDATIONS and mae is not None and acc is not None:
        source = "live"
        pub_mae, pub_acc = round(mae, 2), round(acc, 2)
        print(f"  Live MAE: {pub_mae} µg/m³ | Live Acc: {pub_acc}% (n={n})")
    else:
        source = "insufficient_samples"
        pub_mae, pub_acc = None, None
        print(f"  Live metrics hidden until {MIN_LIVE_VALIDATIONS}+ validations (have {n})")

    if args.dry_run:
        print("  DRY RUN — accuracy.json not written.")
        return

    # Preserve training_metrics from the existing file; only refresh live fields.
    out_path = os.path.join(SITE_DATA_DIR, "accuracy.json")
    existing = {}
    if os.path.exists(out_path):
        try:
            with open(out_path) as f:
                existing = json.load(f)
        except (json.JSONDecodeError, OSError):
            existing = {}

    now = datetime.now().isoformat()
    payload = {
        **existing,
        "generated_at": now,
        "last_pipeline_run": now,
        "mae": pub_mae,
        "accuracy_percentage": pub_acc,
        "source": source,
        "sample_count": n,
        "live_validation_count": n,
        "live_per_country": per_country,
    }

    os.makedirs(SITE_DATA_DIR, exist_ok=True)
    with open(out_path, "w") as f:
        json.dump(payload, f, indent=2)
    print(f"  Wrote {out_path}")


if __name__ == "__main__":
    main()
