# 2026-11-17 Open-Meteo retry/backoff hardening for recent weather repair

## Context
Publish was blocked Nov 15-16: the daily pipeline's "Auto-repair recent weather gaps"
step (`scripts/pipeline/repair_recent_weather.py`) failed four consecutive runs:

- Nov 15 run 43737061965: Open-Meteo daily quota HTTP 429 after ~24 min of productive repair.
- Nov 16 run 43812018366: SSL handshake / ReadTimeout to air-quality-api.open-meteo.com, died in 87 s.
- Nov 16 run 43815577193: repaired part of the 2026-11-11 backlog, then died on an
  unretried-after-3-attempts ReadTimeout to air-quality-api.open-meteo.com.
- Nov 16 scheduled run 43881396154: zero rows repaired, ReadTimeout to api.open-meteo.com.

External probing (2026-11-16 ~20:45 IST) showed open-meteo endpoints flapping:
api.open-meteo.com timed out (>50 s) from an independent network, then all three
endpoints (api, air-quality-api, archive-api) answered 200 in <1 s roughly a minute
later. Conclusion: intermittent multi-minute unavailability windows on the source,
not a runner-side network fault and not persistent API degradation.

The existing retry (3 attempts, backoff 2/4/8 s, ~14 s span) is too short to ride
through multi-minute flap windows, so one unlucky call sequence kills the step and
skips inference/publish for the day.

## Decision
Minimal retry/backoff hardening of the open-meteo calls in
`repair_recent_weather.py` only (approved by owner via parent, 2026-11-16, under
standing fix autonomy; the alternative of making the repair step non-blocking vs
publish remains parked pending sign-off):

- attempts 3 -> 5 per call
- backoff 2/4/8 s -> min(90, 5*2^n) = 10/20/40/80 s between attempts

Worst case a flapping call now spans ~2.5-5 minutes, covering observed flap windows.
No other behavior change: same endpoints, same fail-closed semantics, same 10-minute
step budget, same 6000-call daily ledger cap, same finite-cell immutability checks.

## Consequences
- If a flap window outlasts the retry span the step still fails closed (by design);
  the next run resumes the backlog where this one stopped.
- Longer waits consume more of the 10-minute budget on bad days, reducing rows
  repaired that run; backlog drains over more runs but publish is unblocked on
  normal days.
- Daily quota consumption is unchanged on success paths; on 429 days the step can
  still exhaust quota and fail as before (separate, known limitation).

## Verification
Fix pushed 2026-11-17 ~09:30 IST; verification via workflow_dispatch rerun ahead
of the Nov 17 12:30 UTC scheduled run: repair step should pass and
inference/publish should execute. Outcome appended below after the run.

## Related
- docs/decisions/2026-11-15-storage-reclaim-reload.md
- Freshness plateau note: collection max datetime pinned at 2026-11-13 14:00 UTC
  because the OpenAQ S3 archive is lagging (IN: "S3 MISS ... 0 rows inserted
  (archive lag)", REST fallback disabled; US 9,313 / GB 5 / AU 7,740 rows inserted
  Nov 16). Source-lag, not a pipeline stall; monitored separately.

## Addendum 2026-11-17 10:22 IST: budget exhaustion -> graceful partial success

Verification rerun 44013655831 on the retry/backoff fix (6cbed58) showed the fix
working (three consecutive ReadTimeouts rode through a flap window to a 200 in
0.71s; 11 batches / ~470 station-days repaired, 2026-11-14 fully drained, part of
2026-11-13) but the run still failed: the step's internal 10-minute budget was
exhausted mid-backlog and that condition was fail-closed, skipping publish a 5th day.

Owner signed off (2026-11-17 ~10:22 IST) on making budget exhaustion a graceful
partial success, exactly as proposed:
- new `_BudgetExhausted` exception replaces the fail-closed RuntimeError on the
  per-attempt source budget guard and the loop-level 10-minute deadline;
- loop prints REPAIR_PARTIAL and breaks; committed batches stay, derived-feature
  recompute still runs, step exits 0, publish proceeds;
- backlog resumes where it left off on the next run.

Fail-closed unchanged for real faults: quota/429 after 5 attempts, non-retryable
status codes, assertion failures, finite-cell immutability checks, storage guard.
Owner explicitly declined the alternative (raising the internal budget) - slow
runs and still fails on large backlogs. Landed as commit a751c2e.
Verification: rerun dispatched immediately after push; outcome appended below.
