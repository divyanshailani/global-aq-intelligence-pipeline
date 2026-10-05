# GB frozen archive import

This is the original approved current-fleet scope (208 stations). Of those,
166 have missing pre-2026 archive tasks. The frozen manifest has 123,537 tasks,
ordered 2025, 2024, then 2023 through 2015. It does not dynamically widen the fleet.
The compressed manifest hash is checked before every run.

## First start after merge

Owner review and merge must precede any dispatch:

```sh
gh workflow run gb_archive_backfill.yml --ref main -f initialize=true
```

The first manual initialization creates `backfill_state` and seeds cursor 2,000,
with 192,681 checkpointed insertions. An exact read-only comparison of the next
500 files found all 46,770 source keys already in the DB following the lost
legacy commit response. Those are tracked separately as `legacy_recovered_rows`.
`banked_rows` equals `inserted_rows + legacy_recovered_rows` (initially 239,451).
The cursor deliberately stays at 2,000; those files replay with conflict dedupe.
The table is small: one JSON checkpoint row keyed by task ID, not raw archive
content. The existing database user needs CREATE TABLE permission on first start.
No production migration is executed by preparing this PR.

## Continuation and daily priority

The job uses the existing production concurrency group, at job level, with
cancel-in-progress disabled. A running daily job and a backfill slice cannot
overlap. Slices target 15 minutes with 3-minute chunk deadlines, 100 files per
chunk, and a 20-minute job ceiling. They never approach the six-hour ceiling.
A daily run queued during a chunk waits at most the remainder of this short
slice, not hours. At the next chunk boundary the worker exits for daily priority.

A normal time-limited slice explicitly self-dispatches with initialize=false.
The workflow token has actions:write; workflow_dispatch events can be triggered
by GITHUB_TOKEN. If daily is queued, it does not dispatch a continuation; the
daily completion event resumes only an existing unfinished checkpoint. This workflow does not subscribe to its own completion events (GitHub rejects
that design). A timed-out slice resumes on the next daily completion or manual
dispatch, not immediately.

GitHub concurrency has one pending slot and does not guarantee ordering. A
just-arriving daily dispatch can race the final API check. The shared lock still
prevents concurrent writes; a later daily-completion event/manual dispatch
recovers a displaced continuation. Strict queue priority is not promised.

## Runner kill, failure, and restart

Each flush opens a fresh connection after fetch/parse and closes it before the
next fetch or yield. Raw inserts and cursor/counters commit in ONE transaction.
A killed runner either leaves both committed or neither committed. A lost COMMIT
response is resolved by the next run's DB checkpoint, not by an artifact/cache.
ON CONFLICT preserves existing measurements. Each flush checks the live daily
workflow, database size (<28 GiB), and other active writers. NULL/NaN repair
keeps its own guards; this workflow does not enrich weather or change models.

A timeout, cancellation, runner failure, failed continuation dispatch, or an
ordinary script/DB/source error waits for manual dispatch (initialize=false)
or the next daily completion. No system can promise that a GitHub runner never
gets killed; this design makes a kill resumable and avoids the six-hour cliff.
A failed job is visible as failed, never complete. Fetch failures do not advance
the cursor. Bad source values are skipped and counted, not replaced with zero.
