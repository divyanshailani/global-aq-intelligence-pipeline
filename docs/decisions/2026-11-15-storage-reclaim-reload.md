# 2026-11-15: Storage reclaim executed - truncate + reload-retained on raw/clean

## What happened
Executed the owner-approved recovery for the Nov 13-14 read-only storage crisis
(see 2026-11-13-readonly-storage-safeguard.md): fenced all writers, took a fresh
consistent backup, truncated raw_measurements + clean_measurements, reloaded only
the retained rows (raw 30d / clean 92d policy), rebuilt indexes. No spend, autogrow
still off, no schema/model/behavior changes. Owner condition for the go (WhatsApp,
Nov 15 ~16:16 IST): "ok, if you are sure we have the backup" - Gate 1 was a fully
readback-verified backup before any destructive step.

## Timeline (UTC)
- 10:50:35: fence applied (5 GH workflows disabled, aq-supervisor.timer stopped, markers).
- 10:52-10:57: fenced export of both tables, per-block count cross-checks in-script.
  raw: 11,758,792 retained + 16,978,914 archived = 28,737,706.
  clean: 41,115,906 retained + 7,900,934 archived = 49,016,840.
- ~11:10: backup packaged (29x24MB parts, 664,880,794 B), uploaded to Drive folder
  "AQ retention recovery 2026-11-15 consistent-snapshot PRIVATE" (id
  18Wj7tXRKPQ9WfkkzvO8tGnGx1wrjGijI); every part downloaded back and sha256-verified
  against the box manifest (29/29), rebuilt tars match tar-sha256.txt, gzip integrity OK.
- 11:31: TRUNCATE raw_measurements, clean_measurements (single committed statement).
  pg_database_size fell 27,101,763,263 B -> 1,153,029,823 B instantly.
- 11:31-12:18: clean reload (41,115,906 rows, 100k committed batches, ledger
  aq_prune_checkpoint_20261113; script creates it fresh if absent).
- 12:26-15:27: clean verify - full-row SHA256 multiset per block, ALL CHECKS PASSED
  (one restart after an Instabox cooldown froze the connection mid-verify; read-only,
  no data impact; keepalives added to the DSN afterwards).
- 15:29-15:44: raw reload + verify, ALL CHECKS PASSED.
- 15:46-16:37: clean's 5 indexes recreated one at a time with CHECKPOINT between
  (maintenance_work_mem=1GB); pkey restored as PRIMARY KEY (id) constraint.
- 16:40: fence restored (workflows re-enabled, timer re-enabled, markers removed).
- 16:43: daily pipeline re-dispatched (run 43737061965) to refresh published data.

## Results
- pg_database_size(indiaaq): 27.10 GB -> 18.61 GB (25.2 -> 17.3 GiB).
- Azure storage_percent (PT1M): 93.8185% (Nov 14 11:51 IST) -> 70.5409%
  (Nov 15 22:12 IST); 1h avg 69.7930%. 24h chart: flat ~93.8% -> ~25% at TRUNCATE ->
  ~70% after reload+indexes. Safely below the 95% / <5 GiB read-only safeguard.
- Retention now enforced: 0 rows older than cutoffs, 0 NULL-datetime kept rows,
  row multiset identical to the verified backup (53M rows total across both tables).

## Pre-existing stalls found (not caused by this work)
- max(raw.datetime_utc) = Nov 11 10:00 UTC; max(clean.datetime_utc) = Nov 10 14:00 UTC;
  daily_features max(date) = Nov 9. Last successful daily pipeline run: Nov 12.
  Nov 13 + Nov 14 runs failed: ETL attempt 1 died on SSL connection closed during
  bulk_insert_clean_data; attempt 2 died with ReadOnlySqlTransaction (server in the
  storage safeguard). Root cause of the data staleness is the storage incident
  breaking four consecutive pipeline days, not an upstream source outage.
  Nov 15 rerun expected to catch up; freshness outcome appended below when known.

## Lessons
- The backup gate worked as designed: readback verification (part-level sha256 +
  rebuilt-tar sha256 + gzip integrity) is what made the destructive step a non-event.
- Instabox free-plan cooldowns freeze long-lived DB connections without killing them:
  client blocks in poll() on a session the server considers finished (COPY desync).
  Use TCP keepalives (keepalives_idle=60/interval=30/count=5) on every long op, and
  judge "hung" by pg_stat_activity, not by client CPU.
- TRUNCATE releases physical pages immediately - the only no-spend lever that actually
  shrinks Azure billed storage (DELETE+VACUUM does not, VACUUM FULL needs 2x headroom).
- clean's primary key is constraint-backed; DROP CONSTRAINT, restore with ADD PRIMARY KEY.

## Rerun outcome (appended same day)
Daily pipeline rerun 43737061965 (dispatched 16:43 UTC): incremental collection + ETL
SUCCEEDED - first green ETL since Nov 12. Freshness advanced: raw max Nov 11 10:00 ->
Nov 12 16:00 UTC (+14,034 rows since Nov 12 00:00), clean max Nov 10 14:00 -> Nov 12
16:00 UTC (+13,450), daily_features max Nov 9 -> Nov 12. This confirms the staleness
diagnosis: ingestion writes real-time data and the upstream source is healthy; the
4-day gap was caused by the storage-safeguard pipeline failures, now fixed by the
reclaim. Run still ended red: "Auto-repair recent weather gaps" hit Open-Meteo daily
API quota (HTTP 429 "try again tomorrow"), so inference/publish steps were skipped and
the site keeps serving its last published JSON until a run clears the weather step
(quota resets next UTC day). Data remains ~2.5 days behind realtime; the next scheduled
run should advance it further (one run caught up ~1.5-2 days).

## Final outcome 2026-11-17 12:05 IST: incident fully closed

The post-reclaim publish blockage is resolved. Timeline: Nov15 reclaim green ->
publish blocked 5 days by (1) Open-Meteo quota 429, (2) multi-minute open-meteo
flap windows killing short-retry calls, (3) the repair step's fail-closed
10-minute budget vs a multi-day backlog, (4) the data contract's 2d
prediction-age guard deadlocking against the inference step that would refresh
it. Fixes landed 2026-11-17 with owner sign-off at each step: retry/backoff
hardening 3->5 attempts 10-80s (6cbed58), budget exhaustion as graceful partial
success (a751c2e), permanent max_prediction_age_days dispatch input (eb28ecb).
Rerun 44025683139 fully green; publish restored (web main 75f137c);
prediction_log 2026-11-17; storage 57.66%; freshness Nov14 (upstream
source-bound). Full detail: docs/decisions/2026-11-17-open-meteo-retry-backoff.md.
