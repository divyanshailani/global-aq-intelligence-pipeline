#!/usr/bin/env python3
"""Ad-hoc live-DB verification: does the validator actually validate?

The static tests prove the SQL shape. This proves the round-trip against the
real schema by seeding a synthetic matured forecast, running the validator,
and confirming actual_value/error get filled — then removing the seed.

Uses a sentinel run_id so it can never touch real rows.
"""
import os
import sys
import subprocess
import uuid
from datetime import date, timedelta

PIPE = "/Users/divyanshailani/Desktop/pow-eda-pipeline"
sys.path.insert(0, PIPE)

import psycopg2  # noqa: E402
from src.config import DB_CONFIG  # noqa: E402

SENTINEL = uuid.uuid4()
fails, passes = [], []


def check(name, cond, detail=""):
    (passes if cond else fails).append(name)
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}" + (f" — {detail}" if detail and not cond else ""))


conn = psycopg2.connect(**DB_CONFIG)
conn.autocommit = False
cur = conn.cursor()

try:
    # Find a (country, date) that has real pm25 actuals in daily_features.
    cur.execute("""
        SELECT country_code, date, AVG(value)
        FROM daily_features
        WHERE parameter='pm25' AND value IS NOT NULL
          AND date < CURRENT_DATE
        GROUP BY country_code, date
        HAVING COUNT(*) > 5
        ORDER BY date DESC
        LIMIT 1
    """)
    row = cur.fetchone()
    if not row:
        print("  BLOCKER: no historical pm25 actuals available to validate against")
        sys.exit(2)

    cc, target, true_avg = row[0], row[1], float(row[2])
    print(f"\n  seed: country={cc.strip()} target_date={target} actual_avg={true_avg:.2f}")

    # Seed a matured forecast: run_date before target_date (a real forward
    # prediction), predicted deliberately offset so error is non-zero.
    predicted = true_avg + 5.0
    cur.execute("""
        INSERT INTO prediction_log
            (run_id, run_date, country_code, station_id, target_date,
             horizon_days, predicted_value, actual_value, error)
        VALUES (%s, %s, %s, NULL, %s, 1, %s, NULL, NULL)
        RETURNING id
    """, (str(SENTINEL), target - timedelta(days=1), cc, target, predicted))
    seed_id = cur.fetchone()[0]
    conn.commit()
    check("seeded synthetic matured forecast", seed_id is not None)

    # Run the real validator as a subprocess (exactly as cron invokes it).
    r = subprocess.run(
        [os.path.join(PIPE, "venv/bin/python3"),
         os.path.join(PIPE, "scripts/validate_predictions.py")],
        capture_output=True, text=True, cwd=PIPE,
    )
    check("validator exited 0", r.returncode == 0, r.stderr[-300:])

    # Did it fill in the actual?
    cur.execute("""
        SELECT actual_value, error, validated_at
        FROM prediction_log WHERE id = %s
    """, (seed_id,))
    actual, err, validated_at = cur.fetchone()

    check("actual_value populated", actual is not None,
          "still NULL — validator did not match the row")
    if actual is not None:
        check("actual matches daily_features AVG", abs(actual - true_avg) < 0.01,
              f"got {actual} vs {true_avg}")
        check("error = actual - predicted", abs(err - (true_avg - predicted)) < 0.01,
              f"got {err}")
        check("validated_at stamped", validated_at is not None)

    # accuracy.json refreshed?
    acc_path = os.path.join(PIPE, "site_data", "accuracy.json")
    check("accuracy.json exists", os.path.exists(acc_path))
    if os.path.exists(acc_path):
        import json
        a = json.load(open(acc_path))
        check("accuracy.json has live fields",
              "source" in a and "live_validation_count" in a)
        print(f"    -> source={a.get('source')} n={a.get('live_validation_count')} "
              f"mae={a.get('mae')} acc={a.get('accuracy_percentage')}")

finally:
    # Always remove the sentinel row.
    cur.execute("DELETE FROM prediction_log WHERE run_id = %s", (str(SENTINEL),))
    removed = cur.rowcount
    conn.commit()
    print(f"\n  cleanup: removed {removed} sentinel row(s)")
    cur.execute("SELECT COUNT(*) FROM prediction_log WHERE run_id = %s", (str(SENTINEL),))
    leftover = cur.fetchone()[0]
    check("no sentinel rows leaked into prediction_log", leftover == 0)
    conn.close()

print(f"\n{'='*52}")
print(f"  {len(passes)} passed, {len(fails)} failed")
if fails:
    print("  FAILED: " + ", ".join(fails))
print(f"{'='*52}")
sys.exit(1 if fails else 0)
