# Daily pipeline recent-weather recovery

Run 37775437183 failed October 8 at the written-data contract: 2,315 of 3,921 rows lacked temperature (59%, budget 20%). Collection finished. ETL had a transient SSL disconnect, retried successfully, then Phase 3 exhausted its 180-second cap after processing 313/1,637 station-days. Two API retries were logged without endpoint/status/error detail, so their original network cause is not proven. No logged 429/403 was found. The independent NaN worker yielded production and does not fill existing NULLs.

Recovered only the contract's October 3-8 window, using 40-station recent forecast/AOD batches. All 66 requests returned HTTP 200. Filled 9,260 missing cells across 2,315 rows, preserving existing finite cells. No GB archive calls, historical enrichment, fake zeros, model changes, or relaxed thresholds. Recomputed derived rolling weather features from October 3 onward (3,921 rows). Unchanged contract passed with zero failures/warnings and 0% missing temperature, precipitation, and AOD. Observation lag of three days is within the existing six-day limit.

Requested a daily rerun at 18:32 IST; final outcome is pending. The independent NaN watch remains hourly while production is running.

Follow-up correctness concerns found in production code: 250-location requests with 60-second retries can consume the three-minute enrichment cap; AOD helpers return 0.0 if all source hours are missing. Recovery does not use that fake-zero behavior. No production code changes made in this recovery.
