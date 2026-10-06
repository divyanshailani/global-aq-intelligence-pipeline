# GB archive retraining research

Status: proposed workflow; candidates are not promoted.

## Purpose and boundaries

Test whether the 2015-2025 archive improves GB forecasts against persistence,
roll-seven and the existing champion. Use local compute first, with a reusable
Actions research workflow for review. No Modal credits, DB feature writes,
production schema edits, model swaps, frontend changes or deploy steps.

## Data and time alignment

Export current daily PM2.5 feature rows and raw archive-derived daily means using
read-only sessions. The historical archive import contains UTC timestamps copied
to the local field, so raw dates are reconstructed in Europe/London explicitly.
Existing daily values take precedence where station/date keys already exist.
Only positive finite PM2.5 <=500 micrograms/m3 is used, matching the existing
cleaning bounds. Unsupported units stop research rather than being mixed.

Reindex to daily calendar before lags and trailing windows; targets are exact
t+h station/date joins. The production feature implementation uses row-shifts
across gaps; research calendar features therefore differ. Do not deploy these
candidates without separately addressing inference compatibility.

Weather/AOD on absent historical station-days stays NULL. This stage neither
calls enrichment services nor silently fills zero. The experiment tests history
with missing covariates, not a fully weather-enriched archive. Reanalysis-only
EAC4 stays excluded.

## Evaluation

Use the prior simple XGBoost recipe, 25-feature order, horizon-specific calendar
targets and H+1-day purge. Early stopping uses the last 60 days before each cutoff.
Windows include Oct-Dec 2025 winter and Jan-Mar 2026, followed by Apr-May,
Jun-Jul and Aug-Sep 2026. Current-fleet station rows are the test audience;
existing older fleet history can remain training context. Champion scores in
periods overlapping its training are descriptive, not fair holdout wins.

Also evaluate the country-mean 1/7/14/30-anchor interpolation path used for drift.
Final candidate files stay in research output, never models/v12. A better score
is not permission to promote.

## Operations

The reusable workflow exports station-by-station with finite query timeouts,
then trains privately. It is read-only and has no write-capable DB session.
Its 330-minute cap is below six hours. Research artifacts are not durable DB
checkpoints; runner death can require repeating export. Offline tests cover
calendar gaps and targets before workflow review.

Archive contribution is additionally tested in Aug-Sep with an existing-history-only
ablation using the same calendar features and test rows. This separates archive
signal from a lag-alignment change. Final candidates select tree count using the
purged last-60-day validation, then refit that fixed count on all eligible exact
calendar target pairs; that refit is not reported as a holdout evaluation.
