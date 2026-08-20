#!/usr/bin/env python3
"""Refuse to publish site_data that would misrepresent its own freshness.

The frontend renders ``generated_at``, which is set to wall-clock time on every
run and therefore always looks current. It says nothing about whether the
underlying observations advanced. This gate checks both axes before the frontend
repository is touched:

1. Every published artifact must carry today's UTC ``generated_at``.
2. Every country forecast's ``last_data_date`` must be within
   ``MAX_OBSERVATION_LAG_DAYS`` of today, so a silently stalled collector cannot
   keep republishing predictions anchored to old observations.

Exits non-zero on any violation, which blocks the publish step.
"""

from __future__ import annotations

import json
import os
import sys
from datetime import date, datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SITE_DATA_DIR = PROJECT_ROOT / "site_data"
DEFAULT_MAX_LAG_DAYS = 10


def parse_generated_date(raw: str) -> date | None:
    """Return the UTC calendar date of an ISO-8601 timestamp, or None."""
    if not raw:
        return None
    text = raw.strip().replace("Z", "+00:00")
    try:
        stamp = datetime.fromisoformat(text)
    except ValueError:
        return None
    if stamp.tzinfo is not None:
        stamp = stamp.astimezone(timezone.utc)
    return stamp.date()


def main() -> int:
    max_lag = int(os.environ.get("MAX_OBSERVATION_LAG_DAYS", DEFAULT_MAX_LAG_DAYS))
    today = datetime.now(timezone.utc).date()

    prediction_files = sorted(SITE_DATA_DIR.glob("predictions_*.json"))
    required = prediction_files + [SITE_DATA_DIR / "model_meta.json"]

    failures: list[str] = []

    if not prediction_files:
        failures.append(f"no predictions_*.json found in {SITE_DATA_DIR}")

    for path in required:
        if not path.exists():
            failures.append(f"expected artifact missing: {path.name}")
            continue

        try:
            payload = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError) as exc:
            failures.append(f"{path.name}: unreadable ({exc})")
            continue

        generated = parse_generated_date(payload.get("generated_at", ""))
        if generated is None:
            failures.append(f"{path.name}: missing or unparseable generated_at")
        elif generated != today:
            failures.append(
                f"{path.name}: generated_at is {generated}, expected {today}"
            )
        else:
            print(f"OK: {path.name} generated {payload['generated_at']}")

        if path not in prediction_files:
            continue

        raw_last_data = payload.get("last_data_date")
        if not raw_last_data:
            failures.append(f"{path.name}: missing last_data_date")
            continue
        try:
            last_data = date.fromisoformat(str(raw_last_data).strip())
        except ValueError:
            failures.append(f"{path.name}: unparseable last_data_date {raw_last_data!r}")
            continue

        lag = (today - last_data).days
        if lag > max_lag:
            failures.append(
                f"{path.name}: observation lag {lag}d exceeds {max_lag}d "
                f"(last_data_date={last_data})"
            )
        else:
            print(f"OK: {path.name} observation lag {lag}d (last_data_date={last_data})")

    if failures:
        print("\nFreshness assertion failed:", file=sys.stderr)
        for failure in failures:
            print(f"  - {failure}", file=sys.stderr)
        print(
            "\nRefusing to publish: the site would advertise fresh data it does not have.",
            file=sys.stderr,
        )
        return 1

    print(f"\nAll {len(required)} artifacts are fresh (lag budget {max_lag}d).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
