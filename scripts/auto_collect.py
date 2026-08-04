#!/usr/bin/env python3
"""
IndiaAQ Daily Auto-Collector (launchd-safe)
===========================================
Replaces auto_commit.sh to avoid /bin/bash Full Disk Access issues.
Runs data collection + git commit/push entirely in Python.
"""

import subprocess
import os
import sys
from datetime import datetime

PROJECT_DIR = "/Users/divyanshailani/Desktop/pow-eda-pipeline"
LOG_DIR = os.path.join(PROJECT_DIR, "logs")
LOG_FILE = os.path.join(LOG_DIR, "auto_commit.log")
ENV_FILE = os.path.join(PROJECT_DIR, ".env")

# Load .env file into environment
if os.path.exists(ENV_FILE):
    with open(ENV_FILE) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, val = line.split("=", 1)
                os.environ[key.strip()] = val.strip()

# Ensure log dir exists
os.makedirs(LOG_DIR, exist_ok=True)

def log(msg):
    """Append to log file and print."""
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{timestamp}] {msg}"
    with open(LOG_FILE, "a") as f:
        f.write(line + "\n")
    print(line)

def run_cmd(cmd, cwd=None):
    """Run a shell command and return output."""
    result = subprocess.run(
        cmd, shell=True, capture_output=True, text=True,
        cwd=cwd or PROJECT_DIR,
        env={**os.environ, 
             "PATH": "/usr/local/bin:/usr/bin:/bin:/Library/Frameworks/Python.framework/Versions/3.14/bin",
             "HOME": os.path.expanduser("~")}
    )
    if result.stdout.strip():
        log(f"  stdout: {result.stdout.strip()[:500]}")
    if result.stderr.strip():
        log(f"  stderr: {result.stderr.strip()[:500]}")
    return result.returncode

def main():
    log("=" * 50)
    log("AUTO-COLLECTOR STARTED")
    log("=" * 50)

    # Phase 1: Full pipeline (collect + ETL + inference + validate + publish).
    # run_cron_local.sh already runs the collector as its Step 1, so calling
    # the collector here too would double-fetch every day.
    log("Phase 1: Running full pipeline...")
    pipeline_script = os.path.join(PROJECT_DIR, "scripts", "run_cron_local.sh")
    rc = run_cmd(f"bash {pipeline_script}")
    if rc != 0:
        log(f"WARNING: Pipeline exited with code {rc}")
    else:
        log("Phase 1: Pipeline complete.")

    log("AUTO-COLLECTOR DONE")
    log("")

if __name__ == "__main__":
    main()
