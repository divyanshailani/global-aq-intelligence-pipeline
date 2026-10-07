# Open PR review under maintenance-only scope

## PR 21: closed, unmerged

The yield-continuation fix affects only the historical GB archive backfill. A read-only production checkpoint check on October 7 confirmed task gb-current-fleet-pre2026-v1 finished 123,537 of 123,537 files on October 6. There is no unfinished import needing this fix.

Historical work is parked under the October 6 maintenance-only decision. Merging a continuation change now offers no current maintenance benefit. PR 21 was therefore closed without merging. Its code remains in the branch if historical work is explicitly reopened later.

## PR 19: held pending alert-threshold decision

This PR changes the GB drift baseline from 1.5 to 3.5, raising the 1.5x alert boundary from MAE 2.25 to 5.25. It does not change forecast model files or forecast outputs, but it changes production alert behavior. Current main still has baseline 1.5; PR 22 did not supersede this change.

The owner delegated review of the open PRs on October 7. The threshold change is being checked explicitly before merging. PR 19 remains open and unmerged. No model deployment or historical work was resumed.
