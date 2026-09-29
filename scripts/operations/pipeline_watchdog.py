#!/usr/bin/env python3
"""Watchdog: assert the pipeline actually ran, from outside GitHub.

Why this exists. On 2026-09-28 GitHub's scheduler never created the daily run
at all. The 05:43 UTC cron produced nothing by 11:02 UTC (the previous days had
queued at 10:15 and 10:44), so the site would have aged behind an *all-green*
run history - the workflow's own freshness assertion cannot fire from a run
that never starts, and "no failures" is indistinguishable from "no attempts".
The only thing that can catch a dropped schedule is a check that lives outside
the system being watched.

Checks, deliberately external and cheap:
  1. run exists      - did daily_pipeline.yml create a run today (UTC)?
                       Past the grace window: dispatch one, at most once/day.
  2. observations    - max(daily_features.date) vs today (lags 3-4d are normal;
                       the Open-Meteo starvation of 2026-07-25..09-28 was found
                       only by inspection two months later).
  3. weather feed    - om_temperature missing rate over 10 days (NaN-inclusive; starvation
                       signature; genuine starvation is ~100%).
  4. storage         - GiB used vs the 32 GiB provisioned cap (auto-grow is
                       off, so crossing it turns every write into an error).
  5. published site  - last_data_date in the frontend's live predictions_IN.json
                       on GitHub, which is what visitors actually see.

GitHub-style annotations are printed so a manual CI dispatch shows them too.
Dispatches are recorded in data/watchdog_state.json and capped at one per UTC
day; the concurrency group in the workflow is the second guard.

Usage:
    python3 scripts/operations/pipeline_watchdog.py [--no-dispatch]
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import subprocess
import sys
from datetime import date, datetime, timedelta, timezone

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, REPO)
STATE = os.path.join(REPO, "data", "watchdog_state.json")

# cron/launchd ship a minimal PATH (/usr/bin:/bin); gh lives under Homebrew and
# the keychain-free auth in ~/.config/gh/hosts.yml works fine non-interactively.
# Same fix backup_db.sh applies for pg_dump.
if "/opt/homebrew/bin" not in os.environ.get("PATH", ""):
    os.environ["PATH"] = "/opt/homebrew/bin:" + os.environ.get("PATH", "")

PIPELINE_REPO = "divyanshailani/global-aq-intelligence-pipeline"
FRONTEND_REPO = "divyanshailani/global-aq-intelligence-web"
WORKFLOW = "daily_pipeline.yml"

# The workflow's cron is 05:43 UTC, but scheduled runs on this account have been
# observed *queued* at 10:15 and 10:44 UTC - GitHub routinely delays cron
# dispatches by hours under load. So the grace window must comfortably exceed
# the worst queue delay seen, or the watchdog double-dispatches on healthy days.
GRACE_HOURS = int(os.environ.get("WATCHDOG_GRACE_HOURS", "8"))
CRON_HOUR_UTC = 5  # '43 5 * * *'

MAX_OBS_LAG_DAYS = 6      # 2026-09-28 baseline: 3 days is normal
MAX_WEATHER_NULL_PCT = 50.0  # starvation is ~100%; 9.9% is today's noise level
STORAGE_WARN_PCT = 85.0
STORAGE_FAIL_PCT = 92.0
CAP_GIB = 32.0
MAX_PUBLISHED_LAG_DAYS = 10  # matches MAX_OBSERVATION_LAG_DAYS in the workflow


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def sh(argv: list[str], timeout: int = 90) -> subprocess.CompletedProcess:
    return subprocess.run(argv, capture_output=True, text=True, timeout=timeout)


class Report:
    def __init__(self) -> None:
        self.problems: list[str] = []
        self.dispatched = False

    def problem(self, msg: str) -> None:
        self.problems.append(msg)
        print(f"::warning title=Watchdog:: {msg}")


def load_state() -> dict:
    try:
        with open(STATE) as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {}


def save_state(state: dict) -> None:
    os.makedirs(os.path.dirname(STATE), exist_ok=True)
    with open(STATE, "w") as fh:
        json.dump(state, fh, indent=2)


# ---------------------------------------------------------------- check 1
def check_run_exists(rep: Report, allow_dispatch: bool) -> None:
    today = now_utc().date()
    # camelCase: this gh build rejects created_at in --json
    res = sh(["gh", "run", "list", "--repo", PIPELINE_REPO, "--workflow", WORKFLOW,
              "--limit", "10", "--json", "createdAt,status,conclusion,databaseId"])
    if res.returncode != 0:
        rep.problem(f"cannot read workflow runs from GitHub: {res.stderr.strip()[:160]}")
        return
    try:
        runs = json.loads(res.stdout)
    except ValueError:
        rep.problem("gh returned unparsable JSON for the run list")
        return

    todays = [r for r in runs if str(r.get("createdAt", ""))[:10] == str(today)]
    if todays:
        r = todays[0]
        status, concl = r["status"], r.get("conclusion") or ""
        print(f"  run today: {r['databaseId']} {status} {concl}")
        if status == "completed" and concl == "failure":
            # 2026-09-28: the scheduled run existed, died in ETL, and publish
            # silently skipped. "A run happened" is not "the site is fine".
            # No auto redispatch: an ETL failure is usually deterministic
            # (timeout, schema, data), and a human must read the step log.
            rep.problem(
                f"today's scheduled run {r['databaseId']} concluded failure; "
                f"inference/publish were skipped. Check the ETL step log."
            )
        return

    # Nothing created today. Only act once the normal queue delay has passed.
    elapsed = now_utc().hour - CRON_HOUR_UTC
    if elapsed < GRACE_HOURS:
        print(f"  no run yet today ({now_utc():%H:%M}Z); within the "
              f"{GRACE_HOURS}h grace window - GitHub may still queue it")
        return

    rep.problem(
        f"GitHub created NO {WORKFLOW} run today (it is {now_utc():%H:%M}Z, "
        f"{GRACE_HOURS}h past the {CRON_HOUR_UTC:02d}:43Z cron). "
        f"The site ages behind an all-green history when the scheduler drops a run."
    )
    state = load_state()
    if not allow_dispatch:
        print("  dispatch disabled (--no-dispatch)")
        return
    if state.get("last_dispatch") == str(today):
        print("  already dispatched one run today; not dispatching twice")
        return
    disp = sh(["gh", "workflow", "run", WORKFLOW, "--repo", PIPELINE_REPO])
    if disp.returncode != 0:
        rep.problem(f"watchdog dispatch failed: {disp.stderr.strip()[:160]}")
        return
    state["last_dispatch"] = str(today)
    state["dispatched_at"] = now_utc().isoformat()
    save_state(state)
    rep.dispatched = True
    print(f"  DISPATCHED a manual {WORKFLOW} run (the workflow's concurrency "
          f"group prevents it overlapping any backfill).")


# ------------------------------------------------------------ checks 2-4
def check_database(rep: Report) -> None:
    from src.config import DB_CONFIG  # noqa: PLC0415
    import psycopg2  # noqa: PLC0415

    try:
        conn = psycopg2.connect(**DB_CONFIG, connect_timeout=20)
    except Exception as exc:  # noqa: BLE001
        rep.problem(f"database unreachable: {str(exc)[:160]}")
        return
    conn.autocommit = True
    cur = conn.cursor()

    cur.execute("SELECT max(date), now() FROM daily_features")
    latest, server_now = cur.fetchone()
    if latest is None:
        rep.problem("daily_features is empty")
    else:
        lag = (server_now.date() - latest).days
        print(f"  latest observation {latest} (lag {lag}d)")
        if lag > MAX_OBS_LAG_DAYS:
            rep.problem(f"daily_features stalled: newest date is {latest} "
                        f"({lag}d behind the server clock)")

    since = server_now.date() - timedelta(days=10)
    # Missing weather is written as float8 'NaN' (a stored value, so `IS NULL`
    # reads ~0% no matter how starved the feed is). PostgreSQL treats NaN = NaN
    # as TRUE, so the usual `col <> col` idiom matches nothing here; compare to
    # the NaN literal instead. Same predicate as assert_data_contract.missing().
    cur.execute(
        """SELECT count(*),
                  count(*) FILTER (WHERE om_temperature IS NULL
                                     OR om_temperature = 'NaN'::float8)
           FROM daily_features WHERE date >= %s""",
        (since,),
    )
    n, n_null = cur.fetchone()
    if n:
        rate = 100.0 * n_null / n
        print(f"  om_temperature missing over 10d: {rate:.1f}% ({n_null:,}/{n:,})")
        if rate > MAX_WEATHER_NULL_PCT:
            rep.problem(
                f"om_temperature is missing on {rate:.1f}% of the last 10 days: the "
                f"Open-Meteo feed is starving (2026-07-25..09-28 signature)."
            )

    cur.execute("SELECT pg_database_size(current_database())")
    used = cur.fetchone()[0] / 1024 ** 3
    pct = 100 * used / CAP_GIB
    print(f"  storage: {used:.2f} GiB of {CAP_GIB:.0f} GiB ({pct:.0f}%)")
    if pct >= STORAGE_FAIL_PCT:
        rep.problem(
            f"database storage is {pct:.0f}% of the provisioned cap with auto-grow "
            f"OFF; writes will start failing. Run prune_measurements.py."
        )
    elif pct >= STORAGE_WARN_PCT:
        rep.problem(f"database storage at {pct:.0f}% of the cap")
    conn.close()


# ---------------------------------------------------------------- check 5
def check_published_site(rep: Report) -> None:
    """Read what visitors see, from the frontend repo as GitHub serves it."""
    res = sh(["gh", "api", f"repos/{FRONTEND_REPO}/contents/public/data/predictions_IN.json"])
    if res.returncode != 0:
        rep.problem(f"cannot read published predictions: {res.stderr.strip()[:160]}")
        return
    try:
        payload = json.loads(base64.b64decode(json.loads(res.stdout)["content"]))
    except (KeyError, ValueError) as exc:
        rep.problem(f"published predictions_IN.json unparsable: {exc}")
        return
    raw = str(payload.get("last_data_date", ""))[:10]
    if not raw:
        rep.problem("published predictions_IN.json has no last_data_date")
        return
    lag = (now_utc().date() - date.fromisoformat(raw)).days
    print(f"  published last_data_date {raw} (lag {lag}d)")
    if lag > MAX_PUBLISHED_LAG_DAYS:
        rep.problem(
            f"the live site is serving observations from {raw} ({lag}d old); "
            f"the frontend keeps republishing with a fresh generated_at, which "
            f"is why this is checked outside the workflow."
        )


# ---------------------------------------------------------------- escalate
def escalate(rep: Report) -> None:
    """Open a GitHub issue for today's problems, at most once per UTC day.

    Cron output goes to a log file nobody opens, and the workflow's Slack
    notify step holds a placeholder webhook (it itself 'failed' on
    2026-09-28). An issue is the only alarm with a delivery guarantee here.
    """
    if not rep.problems:
        return
    today = str(now_utc().date())
    state = load_state()
    if state.get("last_issue") == today:
        print("  issue already opened today; not repeating")
        return
    body = (
        "Found by `scripts/operations/pipeline_watchdog.py` at "
        f"{now_utc():%Y-%m-%dT%H:%M:%SZ}:\n\n"
        + "\n".join(f"- {p}" for p in rep.problems)
        + "\n\nRunbook: `RUNBOOK.md`.\n"
    )
    res = sh(["gh", "issue", "create", "--repo", PIPELINE_REPO,
              "--label", "pipeline-watchdog",
              "--title", f"Watchdog: pipeline unhealthy {today}",
              "--body", body])
    if res.returncode == 0:
        state["last_issue"] = today
        save_state(state)
        print(f"  opened issue: {res.stdout.strip()}")
    else:
        # Label may not exist yet; retry once without it rather than losing
        # the alarm to a bookkeeping error.
        res = sh(["gh", "issue", "create", "--repo", PIPELINE_REPO,
                  "--title", f"Watchdog: pipeline unhealthy {today}",
                  "--body", body])
        if res.returncode == 0:
            state["last_issue"] = today
            save_state(state)
            print(f"  opened issue (no label): {res.stdout.strip()}")
        else:
            print(f"  COULD NOT OPEN ISSUE: {res.stderr.strip()[:200]}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-dispatch", action="store_true",
                    help="report a missing run but do not dispatch one")
    args = ap.parse_args()

    print(f"=== watchdog {now_utc():%Y-%m-%dT%H:%M:%SZ} ===")
    rep = Report()
    check_run_exists(rep, allow_dispatch=not args.no_dispatch)
    check_database(rep)
    check_published_site(rep)
    escalate(rep)
    print(f"\nwatchdog: {len(rep.problems)} problem(s)"
          + (" - dispatched a recovery run" if rep.dispatched else ""))
    return 1 if rep.problems else 0


if __name__ == "__main__":
    sys.exit(main())
