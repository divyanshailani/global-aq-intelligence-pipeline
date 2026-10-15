# PM2.5 inference cohort correction, 2026-10-15

## Owner decision and scope

The owner approved shipping the PM2.5 parameter filter plus a freshness cutoff
on October 15 after reviewing the mixed-pollutant and stale-station findings.
No model weights, features, baselines, drift thresholds or training changed.

## Defect and chosen cutoff

The inference query selected each station's latest daily_features row without
filtering parameter. Same-date pollutant ties were unspecified. It also mixed
stations whose own last observations were years apart, then labeled every
forecast relative to the country's maximum date.

Inputs now require parameter='pm25' and exactly the country's latest PM2.5
observation date. This is a conservative zero-day within-country cutoff, not
a claim that source data is same-day. The existing site observation-lag gate
still applies. A whole country becoming stale remains a blocking freshness
check; obsolete stations cannot silently pad the current cohort.

## Read-only reproduction before deployment

Existing ONNX models, same database snapshot, unchanged feature list. Columns
are country, old stations, PM10 rows selected, old day-1 PM2.5 forecast,
PM2.5-only day-1, PM2.5 plus aligned-date day-1, aligned stations:

| Country | Old stations | PM10 rows | Old day 1 | PM2.5 only | Aligned day 1 | Aligned stations |
|---|---:|---:|---:|---:|---:|---:|
| IN | 658 | 347 | 68.02 | 50.63 | 51.79 | 524 |
| US | 1618 | 162 | 8.12 | 7.23 | 5.60 | 285 |
| GB | 443 | 68 | 6.37 | 5.87 | 5.76 | 146 |
| AU | 225 | 61 | 6.65 | 5.88 | 4.79 | 107 |

Forecast units are micrograms/m3. Model horizons 1/7/14/30 remain measured
from the latest observation date, not the generation date. India aligned
anchor forecasts are 51.79/65.15/62.13/86.47. These are input-cohort corrections,
not proof of improved out-of-sample MAE. Future realized observations are
required to assess the corrected forecasts.

## Diagnostic caveats, not changed here

- Large India errors predate the October 14 storage prune: an October 8 run
  predicted 73.51 for October 11 versus current observed mean 36.88.
- Current India observations end October 11, GB October 10, US/AU October 11.
  Comparing the October 12 forecast directly to October 11 observations is
  not a realized forecast-error calculation.
- Site metadata test MAEs and validator baselines differ. The live 90-day
  pooled score mixes forecast origins and horizons; it is not a recent-only
  quality guarantee. Saved validation actuals are filled once and can diverge
  from revised station coverage. None of these metrics were altered here.
- Prediction-log horizons are observation-relative. Actual lead time from
  issue date can differ because of source lag. Multiple runs on one day
  are separate records. Recent-origin samples are small and overlapping.

## Verification

New regression checks cover the PM2.5 restriction on both date anchors and
model inputs, and matching each input date to its country anchor. A live
read-only execution returned IN524/US285/GB146/AU107 aligned rows. Deployment
and final public values will be appended after the normal daily workflow.

Targeted inference/weather regression suite: 5 passed. Wider repository contract
suite has an existing inventory failure for unrelated GB research/backfill
scripts already on main; this change does not edit those scripts. The new
cohort regression is registered in SCRIPT_INVENTORY.md.
