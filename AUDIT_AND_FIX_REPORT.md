# Daily Pipeline Audit & Autonomy Plan

**Audited:** 2026-08-20
**Scope:** `.github/workflows/daily_pipeline.yml`, the four public-path scripts it drives, and the last 20 scheduled runs (2026-08-09 → 2026-08-20).
**Verdict:** The public data path is healthy. Every failure in the last 11 days came from either a *cosmetic step running after publication* or a *time budget that no longer matches how long the work actually takes*. No failure in that window corrupted or blocked the published site.

---

## 1. Why the job failed on 19 and 20 August while the site shows fresh data

Both runs published successfully and then failed on the last step.

| | 2026-08-19 | 2026-08-20 |
|---|---|---|
| Run | [32221680176](https://github.com/divyanshailani/global-aq-intelligence-pipeline/actions/runs/32221680176) | [32337907467](https://github.com/divyanshailani/global-aq-intelligence-pipeline/actions/runs/32337907467) |
| Steps 1–13 | all success | all success |
| `Publish generated site data` | success, 06:23:31Z | success, 06:23:55Z |
| Failing step | `Check persistent drift and open issue` | `Check persistent drift and open issue` |
| Error | `could not add label: 'model-drift' not found` | `could not add label: 'model-drift' not found` |

The frontend commits prove publication happened: `e4812ce5` (2026-08-19T06:23:29Z) and `9ae81fd6` (2026-08-20T06:23:53Z), both `auto: daily V12 predictions`. The site is genuinely current — `public/data/accuracy.json` on `main` carries `generated_at: 2026-08-20T06:23:52`.

### Root cause chain

1. On 2026-08-18 commit `d2971a2` added a final step that opens a GitHub issue when drift is detected.
2. That step calls `gh issue create --label "model-drift"`. **The `model-drift` label never existed in the repo** — confirmed against `gh label list`, which had 20 labels, none of them `model-drift`.
3. `gh` treats an unknown label as a hard error and exits 1.
4. The step ran with `set -Eeuo pipefail` and no `continue-on-error`, so a failed advisory notification turned the whole run red.
5. The step is positioned *after* `Publish generated site data`, so by the time it failed the frontend was already committed, pushed, and SHA-verified.

Why it did not fail on 2026-08-18, the first scheduled run after the step landed: GB's live MAE that day was 2.14, below the 1.5× threshold, so `accuracy.json` carried `drift_warnings: []`. The step logged `No drift warnings today` and exited 0 before ever touching `gh`. The label bug stayed latent for exactly one day and surfaced the moment drift reappeared on the 19th.

**The paradox is therefore not a data problem at all.** A red X on those runs meant "the drift notification could not be filed", not "the site is stale". That is precisely the failure mode worth eliminating: an alerting channel that lies about the health of the thing it monitors.

---

## 2. Why the pipeline errors "every now and then"

Five distinct failure classes appear across the audited window. Only two of them ever touched the public data.

### Class A — Post-publish advisory step fails the job (2026-08-19, 2026-08-20)
Described above. Public impact: **none**. Signal quality: **actively harmful** — it trains you to ignore red runs.

### Class B — Collection exceeds its 20-minute budget (2026-08-10, 2026-08-11)
```
2026-08-11T06:55:37Z ERROR: incremental collection hit the 20-minute budget
##[error]Process completed with exit code 124
```
On 2026-08-11 the collector was 271/350 stations into the last country when `timeout 20m` killed it. This is *correct* behaviour by design — the step deliberately blocks ETL/inference/publish rather than publishing partial data, and checkpoints let the next run resume. But the budget had no slack: commit `1752f36` (2026-08-11 19:28 IST) parallelized S3 fetching and dropped steady-state collection from ~26 minutes to 6–9 minutes, which is why this class stopped recurring after 2026-08-11. It will return the first time OpenAQ is slow, because a single overrun still burns the entire day.

Steady-state collection times since the fix: 6m11s, 6m56s, 7m06s, 7m11s, 7m19s, 8m35s, 11m58s. The 20-minute budget is adequate but a bad day has nowhere to go.

### Class C — ETL exceeds its 15-minute step timeout (2026-08-14)
```
2026-08-14T07:25:37Z ✅ ETL Pipeline Complete! (12m 6s)
2026-08-14T07:28:33Z ##[error]The action 'ETL (retry transient database failures)' has timed out after 15 minutes.
```
The ETL *finished its work* at 12m06s, then spent nearly 3 more minutes on the post-run `COUNT(*)` diagnostic and blew the ceiling. Commit `4c6c631` fixed the counting (it now reads `pg_class.reltuples` estimates instead of scanning), so this exact failure is closed. The residual risk is the margin: observed ETL durations are 11m17s, 11m52s, 12m14s, 12m17s, 13m24s, 14m46s against a 15-minute ceiling. That is a 14-second margin on a bad day.

The bad days are driven by upstream weather API timeouts, visible on 2026-08-19:
```
⚠️ Skipped: Station 2239 on 2026-08-16: 🚨 API Exhaustion. All 3 retries failed for
https://api.open-meteo.com/v1/forecast. Error: Read timed out. (read timeout=15)
```
Each exhausted station costs 45 seconds of wall clock. The ETL's own Phase 3 budget caps enrichment at 3 minutes, so this degrades gracefully in terms of *data* — but it eats the step's timeout headroom.

### Class D — Frontend publish race
Not yet observed as a failure, but structurally present. The publish step commits, then makes up to 3 rebase attempts with a `git reset --soft origin/main` fallback, then up to 3 push attempts, then asserts `local_sha == remote_sha`. The bot rewrites `origin/main` daily and history has been force-pushed at least once (`10e029a`). If a Vercel-triggered or manual commit lands inside that window all 6 attempts can burn and the step exits 1 — which *would* be a genuine publish failure worth alerting on.

### Class E — Validation backlog that never drains
Pending country forecasts across successive runs: 144 → 159 → 168 → 217 → 246. Each run logs 120 new rows (4 countries × 30 horizons) and validates 21–53. The backlog grows because:
- **Observation lag.** `last_data_date` on published predictions is 2026-08-16 against a run date of 2026-08-20 — a 4-day lag. A forecast cannot be scored until its target date has both passed *and* been ingested.
- **Permanently unvalidatable rows.** `validate_predictions.py` selects pending rows with `target_date >= run_date`. Rows written from a stale anchor (target date already in the past at prediction time) never match that filter and sit pending forever. 12 such rows exist in the archive database.

Public impact: **none** — `accuracy.json` reports 326 samples over a 90-day window and the numbers are stable (MAE 5.13 → 5.19 → 5.34). This is housekeeping, not a defect.

### Not a failure class, but worth recording: the local `.env` does not point at production
`POSTGRES_HOST` in the local `.env` resolves to a `globalaqi-archive…` server (`current_database()` = `indiaaq`), reporting `raw_measurements` ≈ 6.36M and `daily_features` ≈ 1.65M. The CI run on 2026-08-20 reported `raw_measurements` 42,790,508 and `daily_features` 849,729 from the `POSTGRES_HOST` secret. **These are different servers.** Any local DB audit is describing the archive, not what the site serves.

Related exposure, worth a separate decision: `.agy_context.md` is tracked in this public repo and names the production PostgreSQL host, the Azure resource names, and a former VM IP in plain text. Those are not credentials, but they narrow an attacker's search space for free. Consider moving that file behind `.gitignore` (see P7).

---

## 3. Fixes applied in this pass

All changes are in `.github/workflows/daily_pipeline.yml` unless noted.

| # | Fix | Class addressed |
|---|---|---|
| 1 | Created the `model-drift` label in the repo | A |
| 2 | `Check persistent drift and open issue` → `continue-on-error: true`, `set -Euo pipefail` (dropped `-e`), and it now self-creates the label if absent | A |
| 3 | `Final database health check` → `continue-on-error: true` (it also runs after publication) | A |
| 4 | New `Assert generated data is fresh` step **before** the frontend is touched (`scripts/pipeline/assert_site_data_fresh.py`): every `predictions_*.json` and `model_meta.json` must carry today's UTC `generated_at`, and each country's `last_data_date` must be within 10 days. Publication aborts otherwise | site-lies-about-freshness |
| 5 | Collection reworked into a 20-minute primary pass plus a single 8-minute checkpoint-resume pass; step timeout 22 → 32 min | B |
| 6 | ETL step timeout 15 → 20 min | C |
| 7 | Job timeout 45 → 70 min to accommodate the new worst case | B, C |
| 8 | `Notify on failure` now files/updates a `pipeline-failure` GitHub issue as the primary channel, and only calls Slack when the secret actually looks like `https://hooks.slack.com/*` | alerting |
| 9 | Created the `pipeline-failure` label | alerting |

Why fix 8 matters: `SLACK_WEBHOOK_URL` currently holds the literal placeholder `placeholder_add_your_slack_webhook_url`. The old code tested only for non-emptiness, so it POSTed to a bogus URL and swallowed the result with `|| true` — meaning **there was no working failure alert at all**. A GitHub issue needs no external setup and cannot silently no-op.

### Post-fix step order

```
1  Checkout
2  Set up Python 3.11
3  Install dependencies
4  Validate secrets and DB connectivity
5  Incremental collection        (32m: 20m primary + 8m resume)
6  ETL                           (20m, 3 attempts)
7  V12 ONNX inference            (10m, asserts 16 models present)
8  Validate predictions
9  Assert generated data is fresh        ← gate: nothing stale reaches the site
10 Prepare frontend repository
11 Publish generated site data           ← last step that can fail the run
12 Final DB health check                 advisory (continue-on-error)
13 Write run summary                     always()
14 Notify on failure                     failure() → opens pipeline-failure issue
15 Check persistent drift                advisory (continue-on-error)
```

The invariant established here: **a red run now means the public path broke.** Everything after step 11 is advisory.

### Verification

`scripts/pipeline/assert_site_data_fresh.py` was unit-tested locally across four scenarios before shipping: fresh artifacts with a 4-day lag (pass), a 15-day lag (fail), yesterday's `generated_at` (fail), and a missing `model_meta.json` (fail).

End-to-end: run [32348271799](https://github.com/divyanshailani/global-aq-intelligence-pipeline/actions/runs/32348271799) (`workflow_dispatch` on `6889910`, 2026-08-20T08:19Z) completed **success** with all 16 steps green. The relevant evidence from its logs:

```
Assert generated data is fresh
  OK: predictions_AU.json generated 2026-08-20T08:37:02.667121Z
  OK: predictions_AU.json observation lag 4d (last_data_date=2026-08-16)
  OK: predictions_US.json observation lag 3d (last_data_date=2026-08-17)
  All 5 artifacts are fresh (lag budget 10d).

Publish generated site data
  Frontend published and verified at ae1af652795f7bc124bf67c89bf5880103d9fa4f

Check persistent drift and open issue
  Drift warnings detected: GB: live MAE 2.75 > 1.5x test MAE 1.5 (ratio=1.83)
  No open drift issue — creating one
```

The drift path now works as intended: issue [#13](https://github.com/divyanshailani/global-aq-intelligence-pipeline/issues/13) was created instead of failing the run. That issue is the GB false positive described in P1 — it is the first thing to resolve, because a permanently-open drift issue is the same signal-quality failure as the one this commit fixed.

---

## 4. Remaining work, in priority order

### P1 — Refresh the GB test-MAE baseline
GB has flagged drift every day since detection shipped (1.64× → 1.65× → 1.83×) and it is a false alarm. The 1.5 µg/m³ baseline in `TEST_MAE_BASELINES` (`scripts/pipeline/validate_predictions.py`) came from training on **6 stations** (Jan–Jun 2024, mean PM2.5 10.66). The model now scores **335 stations** (mean 6.68). Live MAE of 2.48–2.75 across a 50× larger and cleaner station population is good generalization, not decay.

Two options, in order of preference:
1. Retrain the GB models on post-Jan-2026 data and take the new test MAE as the baseline. Also fixes the accuracy percentage, which is depressed because it divides by a lower mean.
2. Interim: raise the GB baseline to the current holdout MAE on the present station mix and note the provenance in a comment.

Until one of these lands, the drift channel produces a daily false positive — which is the same signal-quality problem as Class A, one layer up.

### P2 — Purge permanently-unvalidatable prediction rows
Add a maintenance query that closes out rows where `station_id IS NULL AND actual_value IS NULL AND target_date < run_date` (12 rows in the archive DB). Either mark them with a sentinel or delete them, so the pending backlog reflects real work.

### P3 — Widen ETL headroom at the source
Fix 6 buys margin but does not address the cause. The Open-Meteo `read timeout=15` × 3 retries costs 45s per exhausted station. Either drop to 2 retries with a 10s timeout, or move Phase 3 enrichment to its own scheduled workflow so the public path never waits on a third-party weather API.

### P4 — Revoke the exposed GitHub PAT
`ghp_WyYP…` was removed from the git remote URL but is presumed still live. Revoke at https://github.com/settings/tokens. This is the only credential from the 2026-08-18 leak purge that remains actionable — the OpenAQ keys are already banned upstream.

### P5 — Either configure Slack or drop the secret
`SLACK_WEBHOOK_URL` holds a placeholder. Fix 8 makes it harmless, but leaving a fake secret in place invites the same false-confidence bug later. Set a real webhook or delete the secret.

### P6 — Reconcile the local `.env` with production
Point local `POSTGRES_HOST` at the production server, or rename the variables so it is obvious that local tooling reads the archive. Right now a local query silently answers a different question than CI.

### P7 — Decide on `.agy_context.md` in a public repo
The file is tracked and names the production PostgreSQL hostname, Azure resource names, and a decommissioned VM IP. No credentials, so this is reconnaissance value rather than a breach. Either untrack it (`git rm --cached .agy_context.md` plus a `.gitignore` entry) or redact the infrastructure identifiers. Untracking alone does not remove it from history — that needs the same `git filter-repo` treatment used on 2026-08-18, which force-pushes and invalidates clones, so it is a deliberate choice rather than a cleanup.

---

## 5. Autonomy checklist

For the pipeline to run unattended, each of these must hold. Current status marked.

- [x] Scheduled daily at `43 5 * * *` with `workflow_dispatch` escape hatch
- [x] Serialized against backfill via `concurrency: global-aqi-production-database`, `cancel-in-progress: false`
- [x] Secrets validated before any work starts
- [x] Collection is checkpointed and resumable, with a resume pass inside the same run
- [x] ETL retries transient DB failures 3× with backoff
- [x] Inference asserts all 16 ONNX models are present before running
- [x] Freshness gate blocks publication of stale artifacts
- [x] Frontend push retries and verifies the remote SHA
- [x] Failures open a GitHub issue (no external setup required)
- [x] Advisory steps cannot fail the run
- [x] Drift detection self-heals its own label
- [x] Verified green end-to-end on run 32348271799
- [ ] Drift channel free of known false positives — **blocked on P1 (GB baseline); issue #13 is open right now for this reason**
- [ ] Validation backlog bounded — **blocked on P2**
- [ ] Third-party weather API off the critical path — **blocked on P3**

## 6. Evidence index

| Claim | Source |
|---|---|
| Aug 19/20 failed only on step 15 | `gh run view {32221680176,32337907467} --json jobs` |
| `'model-drift' not found` | `gh run view 32337907467 --log-failed` |
| `model-drift` label absent | `gh label list` (20 labels, no match) |
| Site published on both failed days | frontend commits `e4812ce5`, `9ae81fd6` |
| Site data current | `public/data/accuracy.json` `generated_at: 2026-08-20T06:23:52` |
| Collection budget overrun | run 31465603390 log, exit 124 at station 271/350 |
| ETL finished then timed out | run 31778175187: complete at 12m06s, timeout at 15m |
| Weather API exhaustion | run 32221680176 log, `api.open-meteo.com` read timeout |
| Backlog growth 144→246 | validation lines in runs 31930366001, 32000172618, 32105117634, 32221680176, 32337907467 |
| 12 unvalidatable rows | `prediction_log` query, archive DB |
| Local ≠ production DB | local `reltuples` 6.36M/1.65M vs CI 42.79M/849,729 |
| GB drift false positive | `accuracy.json` GB ratio 1.83 vs 6-station training baseline |
| Aug 18 stayed green by luck | `accuracy.json` at frontend `48279b41`: GB MAE 2.14, `drift_warnings: []` |
