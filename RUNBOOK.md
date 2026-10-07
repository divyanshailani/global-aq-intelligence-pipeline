# GlobalAQI Operations Runbook

What runs where, how to tell healthy from broken in 60 seconds, and the fix
for each failure mode this system has actually produced (every entry below is
grounded in a dated incident or a live check, not a hypothetical).

## 60-second health check

```bash
cd /Volumes/ssd/projects/pow-eda-pipeline
set -a; source .env; set +a
python3 scripts/operations/pipeline_watchdog.py --no-dispatch   # exit 0 = healthy
```

It answers, from outside GitHub, the five questions that matter:
did today's run exist, did observations advance, is the weather feed alive,
is storage under the cap, and is the *published site* (not just the DB) fresh.
Remove `--no-dispatch` to let it auto-recover a missing run.

## Who runs what

| Schedule | What | Where | Logs |
|---|---|---|---|
| 05:43 UTC (`43 5 * * *`) | `daily_pipeline.yml`: collect → ETL → contract → inference → publish | GitHub runner | Actions UI |
| 17:30 / 20:30 / 23:30 IST + boot | `pipeline_watchdog.py` (launchd `com.globalaqi.watchdog`) | this Mac | `~/Library/Logs/globalaqi/watchdog.log` |
| Sun 02:00 | `backup_db.sh` (crontab) | this Mac | `logs/backup_cron.log` |

The watchdog exists because a scheduled run can simply never be created: on
2026-09-28 GitHub's scheduler produced nothing at all (previous days queued
up to 6h16m late). A workflow cannot alert from a run that never started, so
the check lives off-GitHub. It dispatches at most one recovery run per UTC
day (`data/watchdog_state.json`) and opens at most one issue per day
(see issue #16). launchd not cron: cron skips ticks while the Mac sleeps,
launchd coalesces them to one run on wake.

## Workflow gates, in order, and what each catches

1. **Incremental collection** (32m budget, 20m+8m passes) — checkpointed;
   a `timeout 124` after the resume pass intentionally blocks everything
   downstream rather than publishing from a partial day.
2. **ETL** (step 34m, **each attempt 15m via `timeout`**) — on 2026-09-28 a
   healthy pass took 14m40s at 11Z but the next attempt blew through the old
   flat 20m step cap and killed all three retries with zero used. Per-attempt
   budgets now keep the retry loop alive.
3. **Assert written data contract** (`assert_data_contract.py`, blocking) —
   the §20 class: a write succeeds and everything downstream reports green.
   Zero NULL `country_code` in the ETL window (inference filters on it — that
   bug made the site show June as "today"), observation lag ≤ 6d, weather/AOD
   NULL budgets, prediction run ≤ 2d old across 4 countries.
4. **Inference + validate + `assert_site_data_fresh.py`** (blocking) —
   `generated_at` is always "now", so this checks the *observations* inside
   the JSON advanced; it gates publish.
5. **Final health check** (advisory) — storage vs the 32 GiB cap with auto-grow
   OFF; ≥85% emits `::warning::`. Advisory because it runs *after* publish; a
   blip there must not repaint a good publish red.
6. **Drift check** — live MAE > 1.5× test MAE opens/commenting issue (pattern
   today: GB live 4.31 vs test 1.5 → issue #13 stays open, expected for GB).

## Failure modes and fixes

**"Site shows an old date but pipeline is green."** Check
`public/data/predictions_IN.json → last_data_date` vs `generated_at`. Fresh
`generated_at` over stale `last_data_date` means the freshness gate was
bypassed or the run that published predates it. The watchdog checks exactly
this on the *live* frontend repo.

**Run concluded `failure` in ETL.** Read the step log (Actions UI → the
failed step, not the raw zip). If `timed out after 20 minutes` style: the
15m/attempt budget now converts that to one failed *attempt* with two retries
remaining. If the DB is mid-load, re-dispatch:
`gh workflow run daily_pipeline.yml -R divyanshailani/global-aq-intelligence-pipeline`
(the `global-aqi-production-database` concurrency group prevents overlap).
Redispatching a deterministic failure (schema, code) only re-dies — read first.

**Contract check fails on `country_code NULL`.** A write path is omitting the
column (V2-batch-refactor signature, fixed 2026-07 via UPDATE-from-stations
patch in `bulk_insert_features`). Until fixed, publish stays blocked — that is
the gate doing its job; the site keeps yesterday's correct numbers instead of
today's wrong ones.

**Storage ≥85% / writes failing.** Auto-grow is off on
`globalaqi-archive` (hard 32 GiB). Run `scripts/operations/prune_measurements.py`
(archive → reload → verify against the S3-rebuildable horizon; it truncates,
read its header first). 2026-09-28: 25.6 → 19 GB.

**Weather NULL rate alarm.** Open-Meteo free tier is 10k calls/day/IP; the
starvation 2026-07-25..09-28 looked like ~100% NULL. Batch enrichment since
12.2.0 should keep this near noise (today: 3%). If high: check
`fetch_daily_weather.py` in the run log for API errors; the swarm scripts are
the manual backfill path.

**Watchdog issue opened, nothing dispatched.** Either the grace window (8h
after 05:43Z — GitHub has queued runs 6h16m late) hadn't elapsed, or a run
existed but failed (failures are reported, not auto-retried: deterministic
failures loop). Read the linked run before redispatching.

## Thresholds (env-tunable, baselines measured 2026-09-28)

| Knob | Default | Why |
|---|---|---|
| `MAX_OBS_LAG_DAYS` | 6 | normal lag is 3-4d (OpenAQ publishes behind) |
| weather NULL % | fail 20 / warn 10 | measured 9.9% from backfill stragglers |
| AOD NULL % | fail 75 / warn 55 | AOD is missing for **30.93%** of `daily_features` rows (1,759,755) overall and **29.8%** of India's; missing values are stored as float8 `NaN` (a value, not a NULL), so the guards count `(col IS NULL OR col = 'NaN'::float8)` - NOT the usual `col <> col`, which matches nothing because PostgreSQL makes NaN = NaN true - and this budget is NaN-inclusive. The historical ~33% overall figure was roughly right, the 63.5% India figure was not; what causes the missingness is not established. |
| `WATCHDOG_GRACE_HOURS` | 8 | worst observed scheduler queue delay |

Set them in `.env` (the scripts source `src/config.py` → dotenv). A gate that
cries wolf on day one is a gate people mute; every default above is ≥2× the
measured healthy baseline while still catching the ~100% starvation signature.

## Drift baselines (updated 2026-10-05)

`scripts/pipeline/validate_predictions.py` compares live country-aggregate MAE to a baseline and flags drift at 1.5x. Baselines: IN 22.0 (Oct-Feb) / 3.0 (Mar-Sep), AU 2.0, US 2.5, GB 3.5 (constant; no winter data yet, re-check Dec-Jan). They are country-mean MAE, not per-station MAE. Re-measure them whenever models are swapped; update `TEST_MAE_BASELINES` / `SEASONAL_BASELINES` in the same PR.

## launchd quirks (learned the expensive way, 2026-09-28)

- `StandardOut/ErrPath` on the external `/Volumes` SSD → job dies with exit 78
  `EX_CONFIG` before the program runs. Log paths must live on the boot volume
  (`~/Library/Logs/globalaqi/`); `WorkingDirectory` on /Volumes is fine.
- cron/launchd PATH is `/usr/bin:/bin`; the watchdog prepends `/opt/homebrew/bin`
  itself so `gh` resolves. `gh` auth is file-backed (`~/.config/gh/hosts.yml`),
  so it works non-interactively.
- Reinstall after plist edits: `bash scripts/deployment/install_watchdog.sh`
  (bootout + bootstrap + kickstart; check `launchctl print gui/$(id -u)/com.globalaqi.watchdog`).
- Uninstall: `launchctl bootout gui/$(id -u)/com.globalaqi.watchdog`.

## Credentials trap (recurring)

VM SSH password ≠ Azure PostgreSQL password (rotated independently; mixing
them breaks CI). Authoritative values: `CREDENTIALS.md` (untracked). The
`.env` `POSTGRES_HOST` is `globalaqi-archive.postgres.database.azure.com` —
that IS the production database (retired `globalaqiserver` was renamed out of
service; see commit "guard live archive host").

## Manual recovery one-liners

```bash
# dispatch the daily pipeline now (safe: concurrency group serialises writers)
gh workflow run daily_pipeline.yml -R divyanshailani/global-aq-intelligence-pipeline
# today's runs + conclusions
gh run list -R divyanshailani/global-aq-intelligence-pipeline \
  --workflow daily_pipeline.yml --limit 5
# what the live site actually serves
gh api repos/divyanshailani/global-aq-intelligence-web/contents/public/data/predictions_IN.json \
  --jq .content | base64 -d | python3 -c "import sys,json;d=json.load(sys.stdin);print(d['last_data_date'],d['generated_at'])"
# contract + watchdog, local
python3 scripts/pipeline/assert_data_contract.py; python3 scripts/operations/pipeline_watchdog.py --no-dispatch
```
