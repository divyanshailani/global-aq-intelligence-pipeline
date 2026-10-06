# GB backfill resumes after transient writers

Status: proposed in PR, not active until merged.

## Observation

Run 37390673539 exited successfully at cursor 73,900 with `daily_first`.
No daily workflow or other DB writer was present at the following live check.
The original worker used `daily_first` for both a daily workflow and any active
external writer, but only daily completion guaranteed a recovery event.

## Decision

Distinguish external writers as `WriterBusy`: retain the checkpoint, exit the
slice as `continue`, and use the existing self-dispatch path. Also run the final
live daily check for `daily_first` exits, since a queued daily run can disappear.
If daily still exists, do not dispatch; its completion remains the recovery path.

The shared production concurrency group, per-flush writer checks, atomic
raw/checkpoint transaction, frozen manifest, and short job ceiling are unchanged.
A continuing external writer can cause another safe short yield, not an overlap.
GitHub pending-slot/start-after-check races remain as documented in the original
workflow. This change does not modify daily, models, enrichment or DB schema.

## Validation

Existing tests plus external-writer continuation and daily-priority cases pass.
Workflow lint and Python compile pass. No direct main edit or merge is part of
this proposal. Separately resumed the already-approved import with initialize=false.
