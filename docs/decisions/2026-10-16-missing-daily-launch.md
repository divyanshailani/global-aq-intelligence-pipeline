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

Pending normal production completion and live publication verification.
