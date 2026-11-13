#!/usr/bin/env bash
# Cron wrapper — one instance at a time, logs to the HDD.
# Proposed crontab line (21:30 IST daily; governance-red-flag-engine runs ~19:45 IST):
#   30 21 * * * /home/pi/projects/sebi-enforcement-decoder/deploy/run.sh
set -euo pipefail
cd "$(dirname "$0")/.."
DATA_ROOT="${SED_DATA_ROOT:-/mnt/hdd/sebi-enforcement-decoder}"   # placeholder until Pi mount confirmed
mkdir -p "$DATA_ROOT/logs"
exec flock -n /tmp/sebi-enforcement-decoder.lock \
  .venv/bin/sedec run >> "$DATA_ROOT/logs/run-$(date +%F).log" 2>&1
