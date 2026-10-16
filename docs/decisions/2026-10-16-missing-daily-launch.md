# Missing daily launch recovery, October 16, 2026

## Incident

At 14:24 IST the active daily workflow had no October 16 run, despite its
05:43 UTC (11:13 IST) schedule. Authenticated workflow listings showed no
queued, running or completed daily run that day. The last completed run was
38719216153 on October 15. No conclusion about GitHub's internal cause can
be made from an absent run. This was a missing launch, not an ETL failure.

Under the owner's standing pipeline reliability/auto-repair authority, a
manual workflow_dispatch started run38876886993 at14:25IST. This retains the
normal production path, concurrency guard, data contracts and publication
checks. No model, threshold, backfill, resource or spending change was made.

## Monitoring gap and correction

The external hourly reviewer only woke for a newer completed run. Therefore
it could not reliably notice a scheduled run that never started. An existing
repository watchdog already had missing-launch recovery logic, but the
external reviewer was not invoking it. Its local-Mac launchd installation is
not evidence that the Mac is online or that this check ran today.

The hourly reviewer now checks both missing launches and completed outcomes.
It runs the repository watchdog with --launch-only, a new bounded mode that
performs authenticated run checks and recovery dispatch only. This mode does
not access the database, fetch source data, or post issues. The existing
eight-hour scheduler grace is preserved because this account has previously
seen delayed launches. Grace is now measured from the exact05:43UTC schedule,
not the integer hour. Recovery is capped at once per UTC day, skips any
existing same-day run, and yields to an active prior-day daily run. The
workflow concurrency group remains the final no-overlap guard.

The hourly wake is relevant either when a new completed daily run exists or
when no current-UTC-day daily run exists after13:43UTC (the8-hour grace).
That is independent of successful-run history. A scratch-state loss cannot
cause an immediate duplicate while today's run is visible in GitHub. CLI
errors are blockers, never proof of a missing run. The reviewer inspects
recovery outcomes and only escalates unrepaired failures.

Three regression tests cover exact grace boundary, same-day/active-run
suppression and the daily dispatch cap. Tests passed using direct Python
execution. Live --launch-only found today's in-progress recovery and did not
dispatch a duplicate.

- Workflow: https://github.com/divyanshailani/global-aq-intelligence-pipeline/blob/main/.github/workflows/daily_pipeline.yml
- Recovery: https://github.com/divyanshailani/global-aq-intelligence-pipeline/actions/runs/38876886993

## Outcome

Recovery run38876886993 completed successfully at14:53IST. All collection,
ETL, data-contract, inference, validation, freshness and publication gates
passed. Recent weather auto-repair reported NO_MISSING_WEATHER, so it made
no repair/source call. Frontend publication verified commit
d4d00eb7d5792a87b175bd7307deb27680ea517c.

Independent live public JSON verification at14:56IST confirmed generation
at09:21UTC onOctober16 and these observation dates/cohorts/day1 values:

| Country | Observation date | Stations | Day1 PM2.5 |
|---|---|---:|---:|
| IN | 2026-10-12 | 526 | 52.40 |
| US | 2026-10-13 | 248 | 6.10 |
| GB | 2026-10-12 | 158 | 7.19 |
| AU | 2026-10-12 | 108 | 5.50 |

All observation anchors advanced one day versus the prior published run.
The reviewed-run checkpoint advanced only after logs and live publication
were inspected. Temporary completion wakes are consumed. Hourly missing-
launch and completed-run review remains active. Routine recovery success
was kept silent under the owner's auto-repair preference.

- Monitor correction: https://github.com/divyanshailani/global-aq-intelligence-pipeline/pull/34
- https://www.globalaqi.live/data/predictions_IN.json
- https://www.globalaqi.live/data/predictions_US.json
- https://www.globalaqi.live/data/predictions_GB.json
- https://www.globalaqi.live/data/predictions_AU.json
