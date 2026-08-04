#!/usr/bin/env python3
"""Ad-hoc verification for MAX_INCREMENTAL_DAYS (commit 495ad84).

Change under test: get_gap_days() previously returned an unbounded
`gap + 1`. The live-API fallback re-fetches every day in that window across
every station, so a long outage turned one nightly run into hours of work.
Now clamped to MAX_INCREMENTAL_DAYS.

Checks the clamp boundary directly, then confirms the real DB path still
returns sane windows.
"""
import os
import sys

PIPE = "/Users/divyanshailani/Desktop/pow-eda-pipeline"
sys.path.insert(0, PIPE)
sys.path.insert(0, os.path.join(PIPE, "scripts"))

import run_daily_collector as rdc  # noqa: E402

fails, passes = [], []


def check(name, cond, detail=""):
    (passes if cond else fails).append(name)
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}" + (f" — {detail}" if detail and not cond else ""))


CAP = rdc.MAX_INCREMENTAL_DAYS

# ── 1. Constant is wired and sane ──────────────────────────────────
print("\n1. Cap constant")
check("MAX_INCREMENTAL_DAYS defined", isinstance(CAP, int))
check("cap is a useful size (2..31)", 2 <= CAP <= 31, f"got {CAP}")
src = open(os.path.join(PIPE, "scripts", "run_daily_collector.py")).read()
check("get_gap_days applies the cap via min()",
      "min(gap + 1, MAX_INCREMENTAL_DAYS)" in src)
check("still floors at 1 day", "max(1, min(gap" in src)

# ── 2. Clamp boundary (the actual regression risk) ─────────────────
print("\n2. Clamp behaviour across gap sizes")


def capped(gap):
    """Mirror of the expression in get_gap_days()."""
    return max(1, min(gap + 1, CAP))


# Small gaps must be UNCHANGED from the old `gap + 1` behaviour.
for gap in range(0, CAP - 1):
    want = gap + 1
    check(f"gap {gap}d unchanged -> {want}d", capped(gap) == want, f"got {capped(gap)}")

# At and beyond the boundary it must saturate, never exceed.
check(f"gap {CAP - 1}d saturates at cap", capped(CAP - 1) == CAP)
for gap in [CAP, CAP + 5, 30, 365, 10_000]:
    check(f"outage {gap}d clamped to {CAP}d", capped(gap) == CAP, f"got {capped(gap)}")

check("never returns 0 or negative", all(capped(g) >= 1 for g in range(0, 400)))
check("never exceeds cap for any gap", all(capped(g) <= CAP for g in range(0, 10_000)))

# ── 3. Real DB path still works ────────────────────────────────────
print("\n3. Live get_gap_days() against the real DB")
windows = {}
for cc in ["IN", "US", "GB", "AU"]:
    try:
        w = rdc.get_gap_days(cc)
        windows[cc] = w
        check(f"{cc}: returns int in 1..{CAP}", isinstance(w, int) and 1 <= w <= CAP,
              f"got {w!r}")
    except Exception as e:
        check(f"{cc}: get_gap_days did not raise", False, str(e)[:80])

if windows:
    print(f"    -> current windows: {windows}")
    check("all four countries resolved", len(windows) == 4)

# ── 4. Unknown country falls back safely ───────────────────────────
print("\n4. Failure path")
w = rdc.get_gap_days("ZZ")  # no rows -> except/None branch -> default
check("unknown country returns safe default", isinstance(w, int) and 1 <= w <= CAP,
      f"got {w!r}")

print(f"\n{'=' * 52}")
print(f"  {len(passes)} passed, {len(fails)} failed")
if fails:
    print("  FAILED: " + ", ".join(fails))
print(f"{'=' * 52}")
sys.exit(1 if fails else 0)
