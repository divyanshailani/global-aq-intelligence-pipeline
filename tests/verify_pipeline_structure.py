#!/usr/bin/env python3
"""Ad-hoc verification for the Global AQI pipeline fixes.

Covers the parts testable without mutating production state:
  1. summarize() math + edge cases (pure function)
  2. run_cron_local.sh structural guarantees (lock, retry, publish, order)
  3. auto_collect.py no longer double-runs the collector
  4. validate_predictions.py SQL targets country-agg rows (station_id IS NULL)
"""
import os
import re
import subprocess
import sys

PIPE = "/Users/divyanshailani/Desktop/pow-eda-pipeline"
sys.path.insert(0, PIPE)
sys.path.insert(0, os.path.join(PIPE, "scripts"))

fails, passes = [], []


def check(name, cond, detail=""):
    (passes if cond else fails).append(name)
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}" + (f" — {detail}" if detail and not cond else ""))


# ── 1. summarize() pure-function math ─────────────────────────────
import importlib.util  # noqa: E402

spec = importlib.util.spec_from_file_location(
    "vp", os.path.join(PIPE, "scripts", "validate_predictions.py")
)
vp = importlib.util.module_from_spec(spec)
spec.loader.exec_module(vp)

print("\n1. summarize() math")
mae, acc, n, per = vp.summarize([])
check("empty input -> (None, None, 0, {})", (mae, acc, n, per) == (None, None, 0, {}))

# perfect predictions -> mae 0, accuracy 100
rows = [("IN", 10.0, 10.0), ("IN", 20.0, 20.0)]
mae, acc, n, per = vp.summarize(rows)
check("perfect prediction -> mae=0, acc=100", mae == 0.0 and acc == 100.0 and n == 2)

# known error: actuals 10,20 preds 12,18 -> abs err 2,2 -> mae 2, mean 15 -> acc 86.67
rows = [("IN", 10.0, 12.0), ("IN", 20.0, 18.0)]
mae, acc, n, per = vp.summarize(rows)
check("mae computed correctly (2.0)", abs(mae - 2.0) < 1e-9, f"got {mae}")
check("acc = (1-mae/mean)*100 = 86.67", abs(acc - 86.6666666) < 1e-4, f"got {acc}")
check("per-country sample_count", per["IN"]["sample_count"] == 2)

# accuracy floors at 0 rather than going negative on huge error
rows = [("GB", 1.0, 100.0)]
mae, acc, n, per = vp.summarize(rows)
check("massive error floors acc at 0 (never negative)", acc == 0.0, f"got {acc}")

# country split + char(2) padding tolerance ('IN ' from CHAR column)
rows = [("IN ", 10.0, 10.0), ("US", 50.0, 40.0)]
mae, acc, n, per = vp.summarize(rows)
check("padded country code 'IN ' matched via strip()", "IN" in per and "US" in per)
check("countries kept separate", per["IN"]["mae"] == 0.0 and per["US"]["mae"] == 10.0)

# zero mean actuals must not divide by zero
rows = [("AU", 0.0, 0.0)]
mae, acc, n, per = vp.summarize(rows)
check("zero-mean actuals -> no ZeroDivisionError", per["AU"]["accuracy_percentage"] is None)

# ── 2. run_cron_local.sh structure ────────────────────────────────
print("\n2. run_cron_local.sh structure")
sh = open(os.path.join(PIPE, "scripts", "run_cron_local.sh")).read()

check("bash -n parses clean",
      subprocess.run(["bash", "-n", os.path.join(PIPE, "scripts", "run_cron_local.sh")],
                     capture_output=True).returncode == 0)
check("acquires run lock", "mkdir \"$LOCK_FILE\"" in sh)
check("clears stale lock (>6h)", "-mmin +360" in sh)
check("releases lock via trap EXIT", 'trap' in sh and 'rmdir "$LOCK_FILE"' in sh)
check("ETL retries 3x", bool(re.search(r"for attempt in 1 2 3", sh)))
check("ETL backs off between attempts", "sleep 60" in sh)
check("publishes frontend (commit+push)", "git push origin main" in sh)
check("pulls before push (avoid non-FF)", "git pull --rebase origin main" in sh)
check("skips empty commit", "git diff --cached --quiet" in sh)
check("runs validation step", "validate_predictions.py" in sh)

# stage ordering: collector -> etl -> inference -> validate -> publish
order = [sh.find(x) for x in ("run_daily_collector.py", "run_daily_etl.py",
                              "predict_v12_onnx.py", "validate_predictions.py",
                              "git push origin main")]
check("pipeline stages in correct order", order == sorted(order) and -1 not in order, str(order))

# ── 3. auto_collect.py de-duplication ─────────────────────────────
print("\n3. auto_collect.py")
ac = open(os.path.join(PIPE, "scripts", "auto_collect.py")).read()
check("no longer invokes collector directly", "run_daily_collector.py" not in ac)
check("delegates to run_cron_local.sh", "run_cron_local.sh" in ac)
check("compiles", subprocess.run([sys.executable, "-m", "py_compile",
                                  os.path.join(PIPE, "scripts", "auto_collect.py")],
                                 capture_output=True).returncode == 0)

# ── 4. validator SQL targets country-agg rows ─────────────────────
print("\n4. validate_predictions.py SQL")
src = open(os.path.join(PIPE, "scripts", "validate_predictions.py")).read()
check("selects station_id IS NULL (country-agg)", "station_id IS NULL" in src)
check("quarantines stale anchors (target_date >= run_date)", "target_date >= run_date" in src)
check("aggregates actuals with AVG over country+date",
      "AVG(value)" in src and "country_code = %s" in src)
check("does NOT join on station_id (the old bug)",
      "WHERE station_id = %s" not in src and "station_id = %s\n" not in src)

print(f"\n{'='*52}")
print(f"  {len(passes)} passed, {len(fails)} failed")
if fails:
    print("  FAILED: " + ", ".join(fails))
print(f"{'='*52}")
sys.exit(1 if fails else 0)
