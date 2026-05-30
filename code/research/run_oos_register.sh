#!/usr/bin/env bash
# OOS forward register snapshot — one-shot, fires at register_at = 2026-05-30T00:00:00Z.
# Pure deterministic cohort build (public Gamma feed, no auth, no git, no secrets).
# The irreversible predict -> freeze -> witness steps are NOT done here.
# Idempotent: if cohort.jsonl already exists, this is a no-op (re-fires are harmless).
set -euo pipefail
cd /home/ubuntu/pythia

LOG=/home/ubuntu/runtime/logs/pythia/oos_register.log
mkdir -p "$(dirname "$LOG")"
COHORT=data/research/backtests/oos_forward_2026-05-30/cohort.jsonl

{
  echo ""
  echo "==== $(date -Is) oos_register fire ===="
  if [ -f "$COHORT" ]; then
    echo "cohort.jsonl already exists -> snapshot already done, skipping (no-op)."
    exit 0
  fi
  echo "building OOS forward cohort (register_at=2026-05-30T00:00:00Z) ..."
  python3 code/research/build_oos_cohort.py
  echo "OK cohort snapshot complete."
  echo "NEXT (Opus 4.8 step, NOT automated here): predict_oos -> freeze_oos seal -> commit+push -> witness."
} >> "$LOG" 2>&1
