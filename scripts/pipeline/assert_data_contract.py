#!/usr/bin/env python3
"""Assert the written data still obeys its contract. Read-only; exits non-zero.

Why this exists. Two failures in this project's history had the same shape: a
write succeeded, every step downstream reported success, and the data was
wrong.

  * The V2 batch refactor dropped ``country_code`` from the feature INSERT.
    ~28k rows landed with a NULL country, the ONNX inference filters on that
    column so it silently skipped the new July data, and the site displayed a
    June forecast as "today's prediction". Nothing failed.
  * The Open-Meteo enrichment starved from 2026-07-25 onward (per-row calls
    against a 10,000/day cap), so ``om_*`` features went NULL for two months
    and the NULLs were trained on. Nothing failed.

Both are invisible to a pipeline that only checks its own exit codes. This
script checks the *data* after the ETL has written it, so the class of bug is
caught at the moment it happens rather than weeks later during an audit.

Checks, all scoped to a recent window so they stay cheap:
  1. country_code NULLs          - the 20k-orphan signature (hard fail)
  2. observation freshness       - did daily_features actually advance?
  3. om_* weather NULL rate      - the starvation signature
  4. AOD NULL rate               - historical starvation signature: this sat at
                                   ~33% overall and 63.5% in India until the
                                   2026-09-28 batch-enrichment + backfill, which
                                   took it to 0.0% (measured 2026-09-29). The old
                                   "clouds block the satellite" explanation was
                                   WRONG - the same geography now reports 0% NULL,
                                   so those NULLs were failed/rate-limited AOD
                                   fetches, not physics. Fails only if the feed
                                   stops answering again.
  5. derived rolling features    - NULL when their inputs are present
  6. prediction_log recency      - did inference record a run?

Thresholds are environment-overridable so CI can tighten or loosen one check
without a code change. Every check prints its measured value, because a gate
that says only FAIL is not debuggable.

    MAX_NULL_COUNTRY_ROWS=0 MAX_NULL_WEATHER_RATE=10 MAX_NULL_AOD_RATE=75 \
    MAX_OBS_LAG_DAYS=3 MAX_PREDICTION_AGE_DAYS=2 CONTRACT_WINDOW_DAYS=5 \
    python -u scripts/pipeline/assert_data_contract.py
"""

from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

import psycopg2  # noqa: E402

from src.config import DB_CONFIG  # noqa: E402

WINDOW_DAYS = int(os.environ.get("CONTRACT_WINDOW_DAYS", "5"))
MAX_NULL_COUNTRY_ROWS = int(os.environ.get("MAX_NULL_COUNTRY_ROWS", "0"))

# Thresholds set from the 2026-09-28 live baseline, not guessed. Genuine
# starvation is ~100% NULL (exactly how 2026-07-25..09-28 looked), so budgets
# sit well above normal noise yet still catch it.
#   measured: om_temperature 9.9% NULL (394 backfill stragglers in window),
#             om_aod 38.0% NULL, rolling features 0%, obs lag 3 days.
MAX_NULL_WEATHER_RATE = float(os.environ.get("MAX_NULL_WEATHER_RATE", "20"))
WARN_NULL_WEATHER_RATE = float(os.environ.get("WARN_NULL_WEATHER_RATE", "10"))
MAX_NULL_AOD_RATE = float(os.environ.get("MAX_NULL_AOD_RATE", "75"))
WARN_NULL_AOD_RATE = float(os.environ.get("WARN_NULL_AOD_RATE", "55"))
# Observations legitimately trail the calendar by 3-4 days (OpenAQ publishes
# behind), so failing at 3 would reject a perfectly healthy run.
MAX_OBS_LAG_DAYS = int(os.environ.get("MAX_OBS_LAG_DAYS", "6"))
WARN_OBS_LAG_DAYS = int(os.environ.get("WARN_OBS_LAG_DAYS", "4"))
MAX_PREDICTION_AGE_DAYS = int(os.environ.get("MAX_PREDICTION_AGE_DAYS", "2"))


def pct(part: int, whole: int) -> float:
    return 100.0 * part / whole if whole else 0.0


class Report:
    """Collects check outcomes so one bad check cannot hide another."""

    def __init__(self) -> None:
        self.failures: list[str] = []
        self.warnings: list[str] = []

    def fail(self, msg: str) -> None:
        self.failures.append(msg)
        # GitHub annotations, so the failure is visible on the run page and not
        # only in a log that nobody opens. Harmless outside Actions.
        print(f"::error title=Data contract:: {msg}")

    def warn(self, msg: str) -> None:
        self.warnings.append(msg)
        print(f"::warning title=Data contract:: {msg}")


def main() -> int:
    # UTC, not date.today(): this machine runs at IST (+5:30), where local
    # midnight arrives 5.5h before UTC midnight, which would overstate every lag
    # by a day in the evening and slide the window. The whole pipeline (ETL,
    # assert_site_data_fresh, prediction_log.run_date) is UTC-denominated.
    today = datetime.now(timezone.utc).date()
    window_start = today - timedelta(days=WINDOW_DAYS)
    print(f"Data contract check | window {window_start} .. {today} "
          f"({WINDOW_DAYS} days) | host={DB_CONFIG['host']}")

    conn = psycopg2.connect(**DB_CONFIG)
    conn.autocommit = True
    cur = conn.cursor()
    rep = Report()

    def one(sql: str, params=None):
        cur.execute(sql, params or ())
        return cur.fetchone()

    # ---- 1. country_code: the orphan signature that faked a whole day ------
    null_cc, total = one(
        """SELECT count(*) FILTER (WHERE country_code IS NULL), count(*)
           FROM daily_features WHERE date >= %s""",
        (window_start,),
    )
    print(f"  rows in window          {total:,} (country_code NULL: {null_cc:,})")
    if total == 0:
        rep.fail(f"ETL window {window_start}..{today} produced ZERO daily_features rows")
    elif null_cc > MAX_NULL_COUNTRY_ROWS:
        rep.fail(
            f"{null_cc:,} of {total:,} rows in the window have a NULL country_code "
            f"(budget {MAX_NULL_COUNTRY_ROWS}). Inference filters on this column, so "
            f"those rows are skipped and the site can show an older date as today."
        )

    # ---- 2. did observations actually advance? -----------------------------
    latest = one("SELECT max(date) FROM daily_features")[0]
    if latest is None:
        rep.fail("daily_features is empty")
    else:
        lag = (today - latest).days
        print(f"  latest observation date {latest} (lag {lag}d)")
        if lag > MAX_OBS_LAG_DAYS:
            rep.fail(
                f"newest daily_features row is {latest}, {lag} days behind today "
                f"(budget {MAX_OBS_LAG_DAYS}d). The collector or cleaning phase stalled."
            )
        elif lag > WARN_OBS_LAG_DAYS:
            rep.warn(
                f"observations lag {lag} days (healthy is <= {WARN_OBS_LAG_DAYS}d); "
                f"collection is slowing but not stalled."
            )

    # ---- 3/4/5. feature NULL rates in the window ---------------------------
    n, n_temp, n_precip, n_aod, n_roll, n_aodvol = one(
        """SELECT count(*),
                  count(*) FILTER (WHERE om_temperature IS NULL),
                  count(*) FILTER (WHERE om_precipitation IS NULL),
                  count(*) FILTER (WHERE om_aerosol_optical_depth IS NULL),
                  count(*) FILTER (WHERE rolling_3day_precip IS NULL),
                  count(*) FILTER (WHERE aod_volatility_index IS NULL)
           FROM daily_features WHERE date >= %s""",
        (window_start,),
    )
    if n:
        print(f"  NULL om_temperature     {n_temp:,} ({pct(n_temp, n):.1f}%)")
        print(f"  NULL om_precipitation   {n_precip:,} ({pct(n_precip, n):.1f}%)")
        print(f"  NULL om_aod             {n_aod:,} ({pct(n_aod, n):.1f}%)")
        print(f"  NULL rolling_3day_precip {n_roll:,} ({pct(n_roll, n):.1f}%)")
        print(f"  NULL aod_volatility     {n_aodvol:,} ({pct(n_aodvol, n):.1f}%)")

        temp_rate = pct(n_temp, n)
        if temp_rate > MAX_NULL_WEATHER_RATE:
            rep.fail(
                f"om_temperature is {temp_rate:.1f}% NULL in the window "
                f"(budget {MAX_NULL_WEATHER_RATE:.0f}%). This is how the "
                f"2026-07-25..09-28 starvation looked: Open-Meteo calls refused, "
                f"rows kept, features left NULL, and the NULLs then trained on."
            )
        elif temp_rate > WARN_NULL_WEATHER_RATE:
            rep.warn(f"om_temperature NULL rate climbing: {temp_rate:.1f}%")

        aod_rate = pct(n_aod, n)
        if aod_rate > MAX_NULL_AOD_RATE:
            rep.fail(
                f"om_aerosol_optical_depth is {aod_rate:.1f}% NULL in the window "
                f"(budget {MAX_NULL_AOD_RATE:.0f}%). Cloud cover legitimately drives "
                f"this to ~1/3 overall and ~2/3 in India; above the budget the "
                f"air-quality host is refusing again (per-IP daily unit budget)."
            )
        elif aod_rate > WARN_NULL_AOD_RATE:
            rep.warn(f"om_aerosol_optical_depth NULL rate climbing: {aod_rate:.1f}%")

        # Derived features must not be NULL where their inputs exist: that means
        # the recompute step was skipped rather than the upstream being missing.
        stuck = one(
            """SELECT count(*) FROM daily_features
               WHERE date >= %s AND om_temperature IS NOT NULL
                 AND rolling_3day_precip IS NULL""",
            (window_start,),
        )[0]
        print(f"  rolling_3day_precip NULL despite weather present {stuck:,}")
        if stuck > 0:
            rep.fail(
                f"{stuck:,} rows have weather but no rolling_3day_precip: the derived "
                f"feature recompute did not run over rows it should have covered."
            )

    # ---- 6. inference recorded a run --------------------------------------
    last_run, run_countries = one(
        """SELECT max(run_date),
                  (SELECT count(DISTINCT country_code) FROM prediction_log
                    WHERE run_date = (SELECT max(run_date) FROM prediction_log))
           FROM prediction_log"""
    )
    if last_run is None:
        rep.warn("prediction_log is empty: no inference run has ever been recorded")
    else:
        age = (today - last_run).days
        print(f"  latest prediction run {last_run} ({age}d ago, "
              f"{run_countries} countries)")
        if age > MAX_PREDICTION_AGE_DAYS:
            rep.fail(
                f"newest prediction_log run is {last_run}, {age} days old "
                f"(budget {MAX_PREDICTION_AGE_DAYS}d): inference is not recording runs."
            )
        elif run_countries < 4:
            rep.warn(
                f"latest prediction run covers only {run_countries} countries "
                f"(AU/GB/IN/US expected)"
            )

    conn.close()
    print(f"\ncontract: {len(rep.failures)} failure(s), {len(rep.warnings)} warning(s)")
    if rep.failures:
        print("REFUSING: the written data violates its contract.")
        return 1
    print("PASS: written data obeys its contract.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
