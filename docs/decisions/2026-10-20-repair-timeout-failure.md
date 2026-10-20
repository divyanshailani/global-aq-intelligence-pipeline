# 2026-10-20: Daily run failed on weather-repair source timeouts

## What happened
- Scheduled run 39407758271 (created 10:24Z) failed at "Auto-repair recent weather gaps". Open-meteo air-quality returned 3 consecutive ReadTimeouts (AOD, 2026-10-15, 6 stations). `repair_recent_weather.py` raises after the 3rd attempt.
- ETL had succeeded. Inference, validation and publish were skipped, so the site kept Oct19 data.
- Detected by the hourly reliability review. No daily was active, so run 39415248775 was dispatched via workflow_dispatch (standing triage autonomy).
- Rerun 39415248775 succeeded, all gates passed, frontend published at ee7114a. Live last_data_date: IN/GB/AU Oct16, US Oct17.

## Cause
Transient upstream timeout. The repair step is fatal after 3 retries, so one source flake blocks publication.

## Action taken
Rerun only. No code, model or threshold changes.

## Suggestion (not implemented, needs owner approval)
Make the repair step non-fatal on persistent source timeouts: log the gap, skip it and continue to publish. This changes pipeline behavior, so it waits for approval.
