# Bounded source retries and singleton repair batches

Daily run 38256258689 passed ETL but the recent weather repair's first forecast request timed out during TLS handshake (10-second connection timeout). No source success or repair update occurred in that failed step. Contract, inference and publication were skipped.

Add at most three attempts for source connection/timeouts and HTTP 408/429/selected 5xx, with short backoff, within the existing ten-minute deadline and per-request API budget checks. Increase connection timeout to 15 seconds, retaining 35-second read timeout. Permanent errors and exhausted retries still fail and alert.

Manual bounded recovery revealed that the endpoint returns a single object for one-coordinate batches. Normalize that response to a one-element list before validating the batch length. Tests cover timeout recovery, three-attempt exhaustion, and singleton response shape.

October 12 live recovery filled 96 rows / 384 missing cells in the recent five-day window from forecast and AOD HTTP 200 source data, preserving existing finite cells with readback. No remaining missing-weather candidates. Recomputed 3,147 recent derived rows. No archive request, fake zero, model or threshold change. Green patched daily still pending.

## Green recovery

Merged via PR #25 to main ac0a8e7af374a09f775432746d2cf2f7e6b698d1. Fresh run https://github.com/divyanshailani/global-aq-intelligence-pipeline/actions/runs/38263043762 completed successfully October 12 at about 14:46 UTC. The repair step reported NO_MISSING_WEATHER after manual recovery; the unchanged contract passed, as did inference, prediction and freshness validation. Frontend publication verified eac093660695f032aded2c73306abd786864db34. Failure notification skipped. Hourly monitoring remains active for real repair activity and failures. This run confirms the no-gap production path, while retry exhaustion/recovery and singleton behavior are covered by direct regression tests and the live singleton recovery.
