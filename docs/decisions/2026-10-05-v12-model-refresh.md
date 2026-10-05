# V12 model refresh: October 5, 2026

## Scope

This PR prepares four replacements: India (IN) at 7, 14 and 30 days ahead, and Australia (AU) at 14 days ahead. Each has both a native XGBoost `model.json` and an ONNX `model.onnx` under `models/v12/<country>/horizon_<days>/`.

The other models stay unchanged. This PR does not merge itself, run Actions, deploy, or perform the live swap. Those steps need separate approval.

## Why change these models?

The useful test is prediction on later dates that were not used for fitting or choosing settings. A model can fit its training season well while doing badly on the next season. That is why a good in-sample score is not enough to keep an old champion, or to promote a new one.

The new simple regularized recipe beat the old India champions in the out-of-time checks. Heavier tuning did not consistently improve on it. We therefore used the simpler recipe rather than selecting the model with the prettiest tuning score. This does not prove a single cause for the old champions' errors: season changes, station coverage and overfitting can all contribute. The comparison supports these specific swaps, not a claim that every model needed replacement.

Recipe: XGBoost learning rate 0.05, depth 6, subsample 0.8, column subsample 0.8, minimum child weight 5 and L2 regularization (`lambda`) 5. Early stopping uses the last 60 days. Slower learning, sampling and regularization constrain the fit rather than chasing every training fluctuation.

Final-fit validation MAE (lower is better):

| Model | New | Persistence | Old champion |
|---|---:|---:|---:|
| IN h7 | 8.63 | 10.19 | 10.86 |
| IN h14 | 9.19 | 10.06 | 14.94 |
| IN h30 | 9.81 | 11.92 | 19.43 |
| AU h14 | 3.28 | 3.69 | 3.56 |

Persistence means using today's value as the future prediction. The final validation window was used for early stopping, so these figures are not an untouched test. Separate out-of-time comparisons motivated the choice. AU h14's improvement is small and should be watched, not sold as a large win.

## Why not AU h30?

Early stopping left AU h30 with one tree. It predicted nearly the same value for every station. A near-constant model can beat another model on average error without being useful for station-level forecasts. We deliberately did not promote it.

## Native and ONNX checks

Production consumes ONNX, so a native training score alone is insufficient. These models use input `X`, float32, shape `[N,25]`, matching the existing feature order. Conversion used onnxmltools, opset 15. Reported native-versus-ONNX maximum difference was at most 1.1e-4 for IN and 3e-6 for AU. Both file formats are replaced together.

## India rises in October and November

A higher forecast than today's pollution is not automatically a bug. From September 30 the new IN h30 forecast implied a 1.64x rise; the same-station rise last year was 1.84x. The models still under-predict sharp onsets. This is a reason to monitor winter behavior, not a promise that the exact rise will recur.

## Drift baselines must use the same quantity as the live check

The live check compares the country-mean forecast with the realized country mean. It averages over stations and interpolates between the 1/7/14/30-day anchors. A per-station MAE is not a valid reference for this aggregate error. The old IN baseline, 27.1, was not comparable.

Backtests of that exact aggregate path:

| Origin window | IN MAE | AU MAE |
|---|---:|---:|
| Jul-Sep 2026, fit before July 1 | 2.90 (old: 8.13) | 1.43 (old: 1.55) |
| Oct-Dec 2025, fit before October 1, 2025 | 23.15 | 2.11 |

IN's winter error is about eight times its summer error. One low constant would keep crying wolf in winter; one high constant would miss problems in summer.

The code now uses IN baseline 22.0 in October-February and 3.0 in March-September. The winter value is slightly below the 23.15 backtest because the final models also see winter 2025. This is a judgement, not a fresh measurement of the final models' future winter error. January-February are extrapolated, not backtested. The Oct-Dec IN 90th-percentile error was 42.3.

AU uses a fixed 2.0, not 1.5: its October-December aggregate MAE was 2.11, and a 1.5 baseline would alert at 2.25. US stays at 2.5 and GB at 1.5; neither was re-measured in this PR.

The drift rule is unchanged: alert when live MAE exceeds 1.5 times baseline. For IN, that means 33 in smog season and 4.5 in summer. See `baseline_for` and `SEASONAL_BASELINES` in `scripts/pipeline/validate_predictions.py`.

## Deliberately outside this PR

- No AU h30, other country/horizon swaps, or further tuning.
- No EAC4 features in the model input. They were historical reanalysis, not forecast-time eligible.
- No DB schema, data repair, collection or inference feature-order changes.
- No merge, workflow dispatch, deploy or live model swap. Review and separate approval remain required.
