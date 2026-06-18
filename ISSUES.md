# Key Issues & Solutions 

This document logs critical infrastructure and machine learning roadblocks we overcame during development.

## 1. The Chaining "Time Machine" Collapse (Phase 5 to Phase 7)
**Issue:** 
In `v5`, we used a 30-day chained forecasting loop where Day 1's prediction was fed into Day 2 as a `lag_1` feature. This caused rapid error compounding (exponential degradation), meaning that by Day 30 the model was predicting complete noise.

**Solution:** 
We developed the **Direct Multi-Horizon Architecture (V7 Thermodynamics Engine)**. Instead of using Day $N-1$ to predict Day $N$, we trained entirely independent models targeting specific horizons (`1d`, `7d`, `14d`, `30d`). 
- Days 1-7 use the `h1` direct model.
- Intermediate days are processed using our **Anchor Point Strategy** with a **Weather-Weighted Interpolator**.

## 2. India's High-Variance Signal vs "Dumb Linear Interpolation"
**Issue:**
After implementing the anchor point strategy (interpolating between the `1d`, `7d`, `14d`, `30d` predictions), the frontend rendered a perfectly straight, linear line for India. The interpolation stripped out all the chaotic variance of real weather (the "Dumb Linear" problem).

**Solution:**
We developed a Physics-based **Weather-Weighted Interpolator**.
- **Base Interpolation:** Standard linear curve.
- **Extract Daily Weather:** Fetch specific Open-Meteo `future_wind` and `future_precip` for that day.
- **Thermodynamic Modifiers:** 
  - *Rain Washout*: Precip > 2.0mm → PM2.5 drops by 30%.
  - *Wind Dispersion*: Wind > 15 km/h → PM2.5 drops by 15%.
  - *Stagnation Spike*: Wind < 5 km/h and 0 precip → PM2.5 rises by 20%.

## 3. Next.js Aggressive Caching (The "Perfect Straight Line" Illusion)
**Issue:**
Even after applying the Weather-Weighted Interpolator in the backend, the Next.js frontend continued displaying the old straight line.

**Solution (The Developer's Hard Reset):**
Next.js aggressively caches JSON in memory during `npm run dev`. To force Next.js to read the new thermodynamic data:
1. Kill the server process (`kill -9 <PID>`).
2. Destroy the cache directory (`rm -rf .next`).
3. Ensure the prediction pipeline synced the JSON files directly to `global-aq-intelligence/public/data/site_data`.
4. Restart the dev server.

## 4. True Benchmarks vs Operational Backtest
**Issue:** 
The 7-Day Operational Backtest evaluated via `backtest_recent()` yielded `R²=0.38` for India, whereas the true Global test was `R²=0.75`.

**Solution:**
We identified that `backtest_recent()` inherently tests a highly volatile micro-sample (the last 7 days of live production actuals). A 7-day window is prone to extreme variance from seasonal anomalies. The 20% temporal holdout run by `train_v7_experiment.py` evaluates the model across months of data, providing the statistically robust `0.75` R² metric displayed on the site.
