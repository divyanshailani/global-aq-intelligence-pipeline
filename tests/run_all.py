#!/usr/bin/env python3
"""Ad-hoc aggregate runner for the Global AQI pipeline verification scripts.

The repo has no canonical test command, so verification lives in
tests/verify_*.py. This runs all of them against the CURRENT workspace and
aggregates the result, giving one fresh pass/fail signal for the changed
collector code (scripts/run_daily_collector.py).
"""
import glob
import os
import re
import subprocess
import sys

PIPE = "/Users/divyanshailani/Desktop/pow-eda-pipeline"
PY = os.path.join(PIPE, "venv", "bin", "python3")

scripts = sorted(glob.glob(os.path.join(PIPE, "tests", "verify_*.py")))
if not scripts:
    sys.exit("BLOCKER: no tests/verify_*.py found")

total_pass = total_fail = 0
failed_files = []

for s in scripts:
    r = subprocess.run([PY, s], capture_output=True, text=True, cwd=PIPE)
    m = re.search(r"(\d+) passed, (\d+) failed", r.stdout)
    if not m:
        failed_files.append(f"{os.path.basename(s)} (no summary; rc={r.returncode})")
        print(f"  {os.path.basename(s):<34} ERROR rc={r.returncode}")
        print((r.stdout + r.stderr)[-400:])
        continue
    p, f = int(m.group(1)), int(m.group(2))
    total_pass += p
    total_fail += f
    if f or r.returncode != 0:
        failed_files.append(os.path.basename(s))
    print(f"  {os.path.basename(s):<34} {p:>3} passed, {f} failed")

print(f"\n{'=' * 52}")
print(f"  TOTAL: {total_pass} passed, {total_fail} failed")
if failed_files:
    print("  FAILING: " + ", ".join(failed_files))
print(f"{'=' * 52}")
sys.exit(1 if (total_fail or failed_files) else 0)
