# Pipeline verification suite

Ad-hoc checks written while fixing the Aug 2026 pipeline failures. Run them
after touching the collector, ETL, scheduler script, or validator.

```bash
cd ~/Desktop/pow-eda-pipeline
python3 tests/run_all.py        # runs every verify_*.py, aggregates pass/fail
```

Individual scripts can be run directly if you want the detailed output.

| File | Covers | DB writes? |
|---|---|---|
| `verify_pipeline_structure.py` | `summarize()` math, lock/retry/publish wiring, validator SQL shape | no |
| `verify_pipeline_ordering.py` | public path runs before backfill, time-box fallback (live 60s→2s test) | no |
| `verify_insert_counts.py` | `insert_measurements()` returns true inserts, paging, S3-fallback trigger | temp table only |
| `verify_gap_cap.py` | `MAX_INCREMENTAL_DAYS` clamp: small gaps unchanged, long outages saturate | no (reads only) |
| `verify_validator_livedb.py` | full validate round-trip against real schema | seeds + deletes one sentinel row |

## What these exist to catch

**Fake insert counts.** `insert_measurements()` used to return `len(rows)`
instead of actual inserts. With `ON CONFLICT DO NOTHING`, re-fetching stored
days reported *"829,121 rows inserted"* while the DB gained zero. That fake
non-zero number convinced the collector S3 had delivered data, so the live-API
fallback never fired and IN/GB silently froze for 4 days.

Note `execute_values()` only sets `cur.rowcount` from its **last page**, so the
fix pages manually and accumulates. `verify_insert_counts.py` asserts this with
a 12,000-row batch.

**Sequence drift.** `stations_id_seq` sat at 2467 while `MAX(id)` was 87854
(explicit ids loaded during the acc1→acc2 migration). `ON CONFLICT (openaq_id)`
guards the natural key but not the PK, so the first new station killed the whole
fallback. `upsert_stations()` now self-heals with `GREATEST(max_id, last_value)`.

**Validator axis mismatch.** Forecasts are logged country-aggregated
(`station_id IS NULL`), but the old validator joined on `station_id` — it could
never match, so accuracy stayed null from Jun 21. The validator scores on
country+date instead.

## Operational facts worth remembering

- OpenAQ's S3 archive lags real time ~3-4 days and 404s per-station silently.
  Recent days must come from the live v3 API.
- Weather/AOD enrichment costs 2 sequential Open-Meteo calls per row and the
  free tier throttles hard: ~10 rows/min. Bounded by `--max-enrich` (default
  300). Skipped rows stay NULL and retry — XGBoost handles NaN natively.
- The live-API fallback re-fetches every day in the window across every
  station, so `get_gap_days()` is clamped to `MAX_INCREMENTAL_DAYS` (7).
  Without the clamp, a 6-day gap meant ~1h of work for IN's 748 stations.
- macOS ships neither `timeout` nor `gtimeout`; the backfill time-box uses a
  background+kill fallback.
- Backfill runs ~600 stations/hr and is checkpointed in
  `logs/backfill_state.json`, so cutting it off mid-chunk is safe.
