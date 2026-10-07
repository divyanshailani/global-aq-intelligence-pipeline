# Recent NaN maintenance: GB archive-weather exclusion

- Why: the bounded recent repair loop could send GB weather NaNs to Open-Meteo archive weather, contrary to its maintenance scope.
- Change: skip the weather source for GB before any request. For affected GB windows, initialize weather values to NULL; the existing update changes only NaN cells. AOD source routing stays unchanged. No zero values are fabricated.
- Validation: dry-run routing passed all 60 combinations of four countries and 15 nonempty NaN masks. GB never requested archive weather; IN/US/AU routing was unchanged. Authenticated guarded limit-2 live scan passed with zero candidates, no source requests or DB repairs; cursor advanced from station 564 to 2533, wraps remained 7.
- Unchanged: rolling last-90-day scope, IN/US/GB/AU with coordinates, non-NaN preservation assertions, workflow/DB-size/writer guards, API budget, source-failure blocker, cursor retention/wrap protocol. No model, deployment, backfill, or EAC4 work.
- Runtime change is in the maintenance worker, not production model code. Routine maintenance returns to the 5-minute cadence after validation.
