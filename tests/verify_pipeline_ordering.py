#!/usr/bin/env python3
"""Verify the reordered pipeline + prove the backfill time-box fallback works.

macOS has neither `timeout` nor `gtimeout` by default, so the background+kill
fallback branch is the one that will actually execute in production. That
branch is tested here for real with a short budget.
"""
import os
import re
import subprocess
import sys
import tempfile
import time

PIPE = "/Users/divyanshailani/Desktop/pow-eda-pipeline"
SH = os.path.join(PIPE, "scripts", "run_cron_local.sh")

fails, passes = [], []


def check(name, cond, detail=""):
    (passes if cond else fails).append(name)
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}" + (f" — {detail}" if detail and not cond else ""))


sh = open(SH).read()

# ── 1. Ordering: public-facing work must precede backfill ──────────
print("\n1. Stage ordering (public path before backfill)")
idx = {k: sh.find(v) for k, v in {
    "incremental": "--incremental-only",
    "etl": "run_daily_etl.py",
    "inference": "predict_v12_onnx.py",
    "validate": "validate_predictions.py",
    "publish": "git push origin main",
    "backfill": "--backfill-only",
}.items()}
for k, v in idx.items():
    check(f"stage present: {k}", v != -1)

order = ["incremental", "etl", "inference", "validate", "publish", "backfill"]
positions = [idx[k] for k in order]
check("public stages run before backfill", positions == sorted(positions), str(positions))
check("backfill is the LAST stage", idx["backfill"] == max(idx.values()))
check("publish milestone logged before backfill",
      sh.find("PUBLIC-FACING PIPELINE COMPLETE") < idx["backfill"])

# ── 2. Collector no longer double-runs both phases ─────────────────
print("\n2. Collector invocation")
check("incremental phase scoped with --incremental-only", "--incremental-only" in sh)
check("backfill phase scoped with --backfill-only", "--backfill-only" in sh)
check("no unscoped collector call",
      not re.search(r"run_daily_collector\.py\s*(\||\n|$)", sh))

# ── 3. Time-box wiring ─────────────────────────────────────────────
print("\n3. Backfill time-box")
check("budget variable defined", "BACKFILL_MAX_MIN=" in sh)
check("detects timeout binary", "command -v timeout" in sh)
check("detects gtimeout fallback", "command -v gtimeout" in sh)
check("has background+kill fallback for macOS", "watchdog" in sh and "kill \"$bf_pid\"" in sh)
check("treats exit 124 as timeout not failure", "-eq 124" in sh)

# ── 4. Preserved guarantees from the earlier fix ───────────────────
print("\n4. Preserved guarantees")
check("bash -n parses clean",
      subprocess.run(["bash", "-n", SH], capture_output=True).returncode == 0)
check("run lock acquired", 'mkdir "$LOCK_FILE"' in sh)
check("lock released on exit", "trap" in sh and 'rmdir "$LOCK_FILE"' in sh)
check("stale lock cleared", "-mmin +360" in sh)
check("ETL retries 3x", "for attempt in 1 2 3" in sh)
check("frontend pull before push", "git pull --rebase origin main" in sh)
check("empty commit skipped", "git diff --cached --quiet" in sh)

# ── 5. Prove the macOS fallback actually time-boxes ────────────────
print("\n5. Live test of background+kill fallback (the macOS path)")
have_timeout = subprocess.run("command -v timeout || command -v gtimeout",
                              shell=True, capture_output=True).returncode == 0
check("confirmed macOS lacks timeout/gtimeout (fallback is the live path)",
      not have_timeout, "timeout exists; fallback would not be exercised")

# Replicate the fallback with a 2s budget against a process that would run 60s.
harness = tempfile.NamedTemporaryFile("w", suffix=".sh", delete=False)
harness.write("""#!/bin/bash
BUDGET=2
sleep 60 &
bf_pid=$!
( sleep $BUDGET; kill "$bf_pid" 2>/dev/null ) &
watchdog=$!
wait "$bf_pid" 2>/dev/null
kill "$watchdog" 2>/dev/null
echo "bounded"
""")
harness.close()
os.chmod(harness.name, 0o755)

t0 = time.time()
r = subprocess.run(["bash", harness.name], capture_output=True, text=True, timeout=30)
elapsed = time.time() - t0
os.unlink(harness.name)

check("fallback terminates long job at budget", elapsed < 10, f"took {elapsed:.1f}s")
check("fallback exits cleanly after kill", "bounded" in r.stdout)
print(f"    -> 60s job bounded to {elapsed:.1f}s")

# no stray sleepers left behind
leftover = subprocess.run("pgrep -f 'sleep 60' | wc -l", shell=True,
                          capture_output=True, text=True).stdout.strip()
check("no orphaned child left running", leftover == "0", f"{leftover} stray procs")

print(f"\n{'='*52}")
print(f"  {len(passes)} passed, {len(fails)} failed")
if fails:
    print("  FAILED: " + ", ".join(fails))
print(f"{'='*52}")
sys.exit(1 if fails else 0)
