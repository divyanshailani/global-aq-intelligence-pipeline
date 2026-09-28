#!/bin/bash
# Install the GlobalAQI pipeline watchdog as a per-user LaunchAgent.
#
# Read the header in com.globalaqi.watchdog.plist first: it explains why
# launchd (sleep-coalescing calendar intervals) rather than this repo's usual
# cron, and why the ticks sit at 17:30/20:30/23:30 IST (= 12/15/18Z).
#
# Re-run after any edit to the plist. `bootstrap` is idempotent per machine:
# `bootout` first clears an already-loaded copy without unloading anything
# else.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
LABEL=com.globalaqi.watchdog
SRC="$REPO_ROOT/scripts/deployment/$LABEL.plist"
DST="$HOME/Library/LaunchAgents/$LABEL.plist"

[[ -f "$SRC" ]] || { echo "ERROR: $SRC not found"; exit 1; }
plutil -lint "$SRC" >/dev/null && echo "plist OK"

mkdir -p "$HOME/Library/Logs/globalaqi"   # launchd will not create it; missing dir = EX_CONFIG
cp "$SRC" "$DST"

UID_NUM=$(id -u)
launchctl bootout "gui/$UID_NUM/$LABEL" 2>/dev/null || true
launchctl bootstrap "gui/$UID_NUM" "$DST"
launchctl kickstart -k "gui/$UID_NUM/$LABEL" 2>/dev/null || true

sleep 2
launchctl print "gui/$UID_NUM/$LABEL" 2>/dev/null | grep -E "state|last exit" || true
echo "Installed. Output: $HOME/Library/Logs/globalaqi/watchdog.log"
