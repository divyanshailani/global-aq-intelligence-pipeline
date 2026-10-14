# 2026-10-14: Recover storage headroom without growing the disk

## Failure and decision

Daily run 38550745071 failed in ETL on `ReadOnlySqlTransaction` at the schema check. Collection also caught read-only INSERT exceptions and reported completion while retaining old data. A full rerun successfully ingested raw data, then lost the SSL connection during the clean-data insert; the retry was read-only again.

Azure Resource Health explicitly attributed both read-only windows to storage close to exhaustion. Physical storage reached 95.0492% at 16:14-16:18 IST and 95.2111% at 21:13-21:15 IST. WAL rose to 992 MiB and 848 MiB respectively. Storage autogrow was disabled. Database size alone understated physical disk pressure.

The owner chose pruning old data rather than spending on storage growth. Autogrow was left off. No models, thresholds or weather-source policy were changed.

## Retention and safe reclamation

Keep hourly raw and clean measurements from 2026-07-14 00:00:00 UTC onward. This is a 92-day window at the decision date, preserving the feature loader's 90-day local-time lookback and UTC margin. Keep all daily_features, stations, prediction tables, models and published data.

Before any truncate:

- Export retained and older rows in primary-key blocks.
- Verify gzip SHA256 and independent decompressed CSV row counts.
- Compare each block with live database counts, including absence of NULL timestamps.
- Save both old and retained data to a private durable backup with part and tar hashes.
- Freeze competing writers and check current writeability.

Ordinary DELETE/VACUUM would not reliably shrink files; VACUUM FULL would need a second table copy on an already-full disk. Use committed TRUNCATE then retained-row COPY instead. Raw was truncated first, freeing headroom for WAL; clean followed only after raw restore and verification.

Each 100,000-row COPY commits atomically with a checkpoint in `aq_prune_checkpoint_20261014`. A restart skips committed batches and does not retruncate. Sequences were set to the maximum retained ID, and tables analyzed. Autovacuum was left running.

## Verified data result

| Table | Retained rows | Archived older rows excluded |
| --- | ---: | ---: |
| raw_measurements | 36,238,324 | 12,250,337 |
| clean_measurements | 34,032,324 | 3,137,212 |

Every retained row was independently compared across all columns and IDs against the original retained archives, using count plus SHA256 row-multiset sum and xor over 22 blocks. All 70,270,648 retained rows matched. All other tables were left untouched.

Raw truncate committed 2026-10-14 22:42:59.367 IST; clean truncate committed 2026-10-15 00:06:58.387 IST. Clean restore completed about 01:31 IST; final value comparisons finished about 01:47 IST. Database size after restore was 22,500,472,511 bytes (20.955 GiB), down about 4.25 GiB from the pre-prune reading of 27,063,662,271 bytes.

The private backup contains both older and retained CSVs, manifests, hashes and restore instructions. Restoring the older history would consume the space intentionally reclaimed. Do not do it as a routine retry.

## Recovery gate still pending

Physical storage and WAL must settle and be checked against Azure metrics before rerunning collection and ETL. An immediate post-restore reading at 01:34 IST was 85.30% with about 2.1 GB WAL, still above the less-than-5-GiB-free safeguard boundary for a 32-GiB disk. Database size alone is not a safe rerun gate. Production publication is not yet verified recovered.

Sources:

- https://github.com/divyanshailani/global-aq-intelligence-pipeline/actions/runs/38550745071
- https://learn.microsoft.com/en-us/azure/postgresql/compute-storage/concepts-storage

## Additional raw-only retention cut

The settled physical reading remained 85.01-85.28% with 2.1 GB WAL, above the documented less-than-5-GiB-free boundary. A CHECKPOINT at 01:57:16 IST succeeded but did not reduce allocated WAL. SQL confirmed 2,113,929,571 WAL bytes, no replication slots or sessions, successful current archiving and only about 40 MB in other databases. A rerun at that headroom was rejected.

Production consumer review showed daily cleaning reads `recent_days + 1` (six days in this workflow); its default is seven days. The collector uses the latest country timestamp. The 90-day feature lookback reads clean_measurements, not raw_measurements. Legacy all-history/single-station helpers are not scheduled production; their older raw input remains in the private backup.

Within the owner's old-data prune decision, raw retention was reduced to 30 days, cutoff 2026-09-15 00:00:00 UTC. Clean retention remains 92 days. The 30-day retained export was filtered from the already verified and privately backed-up 92-day archive, count/SHA256-checked, then matched to independent live source counts before a separate checkpointed truncate/reload.

Final raw retained count: 13,158,992. Additional raw rows excluded: 23,079,332. Total raw rows excluded across both cuts: 35,329,669. Clean retained: 34,032,324; older clean excluded: 3,137,212. Every final raw row and all clean rows were compared across all columns and IDs against retained archives. All daily/site/model tables stayed untouched.

Raw-only second truncate committed 2026-10-15 02:16:47.713 IST; reload completed about 02:36 IST; final value verification finished 02:40 IST. Database size was 17,918,539,455 bytes (16.688 GiB), writable. Physical storage and production publication remain separate gates to verify, not inferred from this database-size reading.
