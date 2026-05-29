"""Cutoff-clean backtest prediction helper.

Cohort 390개 시장에 대해 Opus 4.7이 P(YES) 매긴 결과를 기록.

핵심 원칙 (forward_predict_helper.py와 동일):
- input prompt에 final_prices / market_p_at_freeze / volume / outcomePrices 절대 노출 X
- 보이는 것: id, endDate(date only), question 만
- rationale 본문은 audit용 저장 + sha256

차이점:
- 트랙 디렉토리: data/research/backtests/cutoff_clean_2026-05-29/
- experiment_kind: backtest_paper_only (resolved past markets, knowledge-cutoff buffered)

CLI:
  python code/research/backtest_predict.py prompt --batch 0 --size 30
  python code/research/backtest_predict.py record --json '<batch_json>'
  python code/research/backtest_predict.py status
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO = Path("/home/ubuntu/pythia")
COHORT_DIR = REPO / "data" / "research" / "backtests" / "cutoff_clean_2026-05-29"
COHORT = COHORT_DIR / "cohort.jsonl"
PRED = COHORT_DIR / "predictions.jsonl"

MODEL_ID = "claude-opus-4-7"
BATCH_SIZE_DEFAULT = 30


def _load_cohort() -> list[dict]:
    return [json.loads(l) for l in COHORT.open()]


def _predicted_ids() -> set[str]:
    if not PRED.exists():
        return set()
    return {json.loads(l)["market_id"] for l in PRED.open()}


def cmd_prompt(batch_idx: int, size: int) -> None:
    cohort = _load_cohort()
    start = batch_idx * size
    end = min(start + size, len(cohort))
    if start >= len(cohort):
        print(f"# batch {batch_idx} out of range (cohort n={len(cohort)})", file=sys.stderr)
        sys.exit(1)
    batch = cohort[start:end]
    print(f"# === Backtest Batch {batch_idx} ({start}-{end-1}, n={len(batch)}) ===")
    print("# Task: For each market, output P(YES) ∈ [0.01, 0.99] and a ONE-SENTENCE rationale.")
    print("# Estimate from the question text and resolution date only.")
    print("# Do NOT search for, recall, or assume the outcome — your job is calibrated forecasting.")
    print("# Output strict JSON: {\"batch_idx\":N, \"predictions\":[{\"market_id\":\"...\",\"p_yes\":0.42,\"rationale\":\"...\"}, ...]}")
    print()
    for r in batch:
        # ONLY id, endDate(date), question
        print(f"  - id={r['id']}  end={r['endDate'][:10]}  Q: {r['question']}")
    print()
    print(f"# After Opus generates JSON, run: python code/research/backtest_predict.py record --json '<json>'")


def cmd_record(batch_json: str) -> None:
    payload = json.loads(batch_json)
    preds = payload["predictions"]
    cohort_by_id = {r["id"]: r for r in _load_cohort()}
    already = _predicted_ids()
    ts = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    added = 0; skipped = 0
    with PRED.open("a") as f:
        for p in preds:
            mid = str(p["market_id"])
            if mid in already:
                skipped += 1; continue
            if mid not in cohort_by_id:
                print(f"  ⚠ id={mid} not in cohort, skipping", file=sys.stderr)
                continue
            p_yes = float(p["p_yes"])
            assert 0.01 <= p_yes <= 0.99, f"p_yes out of range: {p_yes}"
            rationale = p["rationale"].strip()
            rec = {
                "market_id": mid,
                "slug": cohort_by_id[mid]["slug"],
                "model": MODEL_ID,
                "p_yes": p_yes,
                "rationale": rationale,
                "rationale_sha256": hashlib.sha256(rationale.encode("utf-8")).hexdigest(),
                "predicted_at": ts,
                "track": "paper-only",
                "experiment": "cutoff_clean_2026-05-29",
            }
            f.write(json.dumps(rec, ensure_ascii=False, separators=(",", ":")) + "\n")
            added += 1; already.add(mid)
    print(f"✅ recorded {added} new (skipped {skipped} dupes)  total={len(already)}/{len(cohort_by_id)}")


def cmd_status() -> None:
    cohort = _load_cohort()
    done = _predicted_ids()
    print(f"cohort  : {len(cohort)} markets")
    print(f"recorded: {len(done)} ({100*len(done)/len(cohort):.1f}%)")
    remaining = len(cohort) - len(done)
    if remaining:
        print(f"remaining: {remaining}")
        next_batch = len(done) // BATCH_SIZE_DEFAULT
        print(f"next batch: {next_batch}  (size={BATCH_SIZE_DEFAULT})")
    else:
        sha = hashlib.sha256(PRED.read_bytes()).hexdigest()
        print(f"\n✅ ALL DONE. predictions.jsonl sha256 = {sha[:16]}…")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    sp = ap.add_subparsers(dest="cmd", required=True)
    p_prompt = sp.add_parser("prompt"); p_prompt.add_argument("--batch", type=int, required=True); p_prompt.add_argument("--size", type=int, default=BATCH_SIZE_DEFAULT)
    p_rec = sp.add_parser("record"); p_rec.add_argument("--json", required=True)
    sp.add_parser("status")
    args = ap.parse_args()
    if args.cmd == "prompt":
        cmd_prompt(args.batch, args.size)
    elif args.cmd == "record":
        cmd_record(args.json)
    elif args.cmd == "status":
        cmd_status()
