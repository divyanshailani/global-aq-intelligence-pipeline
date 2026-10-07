# GB drift baseline: 1.5 to 3.5 (models unchanged)

## Decision
Set the GB drift baseline in `validate_predictions.py` to 3.5. The GB models are not touched.

## Why the old 1.5 was wrong
- The GB champions were built when the fleet had about 6 stations. The fleet is now 150-330 stations (150 in Q1, 330 in Q2, 207 in Q3 2026).
- The live check compares the country-mean forecast (interpolated across h1/7/14/30) with the realized country mean. 1.5 was a per-station-era figure and is not comparable to that aggregate path.
- Live GB MAE was 4.13 on 2026-10-05, 2.75x the old baseline, so it alerted on every run.

## Evidence
Backtest of the current champion on the exact aggregate path, origins May-Sep 2026, targets to Sep 30. The champion looks in-sample before about June, so only later origins are fair:
- Jun 3.76, Jul 2.61, Aug 3.65, Sep 2.97. Jul-Sep origins: mean 3.32, p75 4.4, p90 6.4.
- By lead day: d1 1.9, d7 2.7, d14 3.0, d21 3.3, d30 2.5.
- A refit with the simple recipe gets 2.97 on Aug-Sep origins vs persistence of the country mean at 2.95, and no model beats persistence per station at any horizon. So a swap would not help, and nothing is promoted.
- 3.5 sits at the top of the Jun-Sep range. With the 1.5x rule the alert fires above 5.25, so the current 4.13 passes and a real degradation still shows.

## Limits
- History is about 9 months of usable data and has no winter. The baseline is a constant because the data cannot support a seasonal one. UK winter PM2.5 is likely higher, so expect to revisit.
- Station mix is still shifting.

## Next
- Backfill GB history from the OpenAQ archive (scoping under way).
- Re-evaluate the GB models and this baseline in Dec-Jan.
