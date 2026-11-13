# 2026-11-13: Read-only incident - Azure storage safeguard (repeat of 2026-10-14)

## Timeline (UTC)
- 13:25: scheduled daily run 43433558628 started. Collection completed (IN 749 stations, ~503k rows); ETL attempt 1 ran minutes then `SSL connection closed unexpectedly`; attempt 2 died in 2s at the ETL's first DDL (`ALTER TABLE daily_features ADD COLUMN IF NOT EXISTS`) with `ReadOnlySqlTransaction: cannot execute ALTER TABLE in a read-only transaction`.
- ~13:38: engine-level `default_transaction_read_only` observed `on`, pg_settings source "configuration file" (Azure managed layer, not ARM).
- 14:43: maintenance agent re-dispatched under standing triage autonomy (run 43445432671); failed identically.
- 16:20-17:00: diagnosis. Provider confirmed Azure Flexible Server `globalaqi-archive` (Central India, PG 18.6, 32 GB Premium_LRS P4, autogrow DISABLED). ARM config for default_transaction_read_only: value off, source system-default. Activity log since Nov 10: empty. Azure Monitor storage_percent avg 92.2% at 00:00Z. User confirmed (21:44 IST) he made no changes.
- 17:03: **agent error**: applied `ALTER DATABASE indiaaq SET default_transaction_read_only = off` and re-dispatched (run 43468916313) while the storage-safeguard hypothesis was open - exactly the condition the hold protected. Caught by main at 17:04.
- 17:04: run 43468916313 CANCELLED (setup + partial collection only; ETL and later steps skipped; all data preserved). Override reverted with `ALTER DATABASE indiaaq RESET default_transaction_read_only`. Before/after evidence: pg_db_role_setting went from [('indiaaq', ['default_transaction_read_only=off'])] to []; pg_settings then showed ('default_transaction_read_only','off','default','off') - the managed layer had already released its read-only (flapping safeguard), RESET cannot change a server GUC.
- 17:50-17:55: fine-grain (PT5M) metrics: storage_used ~31.5e9 B (~92% of 32 GB), fluctuating +/-15 MB per 5 min.

## Cause (most-consistent, not proven)
Azure's disk-full read-only protection. Documented rule (MS Learn, concepts-storage-premium-ssd): read-only when usage reaches 95% OR available capacity < 5 GiB. At ~92% of 32 GB, free ~2.5-2.8 GB < 5 GiB line; ETL write bursts (WAL/temp) flap it on/off. ARM untouched + empty activity log + engine source "configuration file" + user denial all fit; a direct privileged SQL action (e.g. ALTER SYSTEM) is not excluded by ARM logs but no component in our stack issues one.

## Why storage is critical again
Oct 15 post-prune: 73.5% (~8.5 GiB free). Nov 13: ~92% (~2.6 GB free). Growth ~0.6 pts/day (~200 MB/day physical incl. WAL overhead).
Current tables (est): clean_measurements 46.8M rows / 19 GB (range 2026-07-14..11-10 - beyond the 92-day policy), raw_measurements 26.8M / 5.4 GB (est 16.8M rows older than the 30-day policy - retention not being enforced), daily_features 2.37M / 1.0 GB (kept fully).

## Recovery plan (owner-approved scope from 2026-10-14: prune oldest, backup-first to his Drive, hash-verified, autogrow stays off, no spend)
1. Archive + prune clean_measurements older than 2026-08-13 (est ~7.5M rows, ~3 GB) via scripts/operations/prune_measurements.py (archive -> hash-verify -> Drive upload -> reload).
2. Same pattern for raw_measurements older than 2026-10-14 (est ~16.8M rows, ~3.4 GB) - script is clean-table-specific; needs its raw-column variant (check Oct 14 history).
3. Expected result: DB ~25.1 GB -> ~18.7 GB; physical ~92% -> ~73%; back under the safeguard line.
4. Then re-dispatch the daily and verify; investigate why raw 30-day retention is not self-enforcing.
NO deletion or rerun before main relays the plan.

## Lessons
- Never override a read-only state while a storage-safeguard hypothesis is open; "intentionality answered" != "storage safety grounded".
- storage_percent/storage_used support PT5M grain on this resource; query fine grain before claiming metric lag.
- The handover's own guardrail (">92%: act immediately, writes are about to fail") was correct.
