#!/usr/bin/env bash
# Daily forward-paper Brier eval: poll resolutions → compute Brier 3-way.
# Logs append to /home/ubuntu/runtime/logs/pythia/forward_eval.log

set -euo pipefail
cd /home/ubuntu/pythia

LOG=/home/ubuntu/runtime/logs/pythia/forward_eval.log
mkdir -p "$(dirname "$LOG")"

{
  echo ""
  echo "==== $(date -Is) ===="
  if ! python3 code/research/verify_freeze.py --quiet; then
    echo "🚨 freeze drift — aborting eval. Manual inspection required."
    exit 1
  fi
  python3 code/research/poll_resolutions.py
  echo "----"
  python3 code/research/brier_evaluator.py
} >> "$LOG" 2>&1
