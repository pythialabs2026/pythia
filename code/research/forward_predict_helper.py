"""Forward paper prediction helper.

Cohort 173개 시장에 대해 Opus 4.7이 P(YES) 매긴 결과를 기록.

핵심 원칙:
- input prompt에 market_p_yes_at_freeze 절대 노출 X (anchoring 방지)
- rationale 본문은 audit용으로 저장하되, hash도 함께 기록 (Pythia 규약과 정합)
- NOT signed: data/predictions/ 가 아니라 data/research/backtests/ 에만 기록

CLI:
  python code/research/forward_predict_helper.py prompt --batch 0 --size 30
      → batch input prompt를 stdout으로. Opus가 읽고 JSON 응답 생성.

  python code/research/forward_predict_helper.py record --json '<batch_json>'
      → Opus 응답 JSON을 predictions.jsonl에 append.

  python code/research/forward_predict_helper.py status
      → 진행률 / 남은 batch.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO = Path("/home/ubuntu/pythia")
COHORT_DIR = REPO / "data" / "research" / "backtests" / "forward_paper_2026-05-28"
COHORT = COHORT_DIR / "cohort.jsonl"
PRED = COHORT_DIR / "predictions.jsonl"
META = COHORT_DIR / "cohort.meta.json"

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
    print(f"# === Batch {batch_idx} ({start}–{end-1}, n={len(batch)}) ===")
    print("# Task: For each market, output P(YES) ∈ [0.01, 0.99] and a ONE-SENTENCE rationale.")
    print("# Do NOT look up market price. Estimate from question + endDate + base knowledge.")
    print("# Output strict JSON: {\"batch_idx\":N, \"predictions\":[{\"market_id\":\"...\",\"p_yes\":0.42,\"rationale\":\"...\"}, ...]}")
    print()
    for r in batch:
        # market_p_yes_at_freeze 의도적 누락
        print(f"  - id={r['id']}  end={r['endDate'][:10]}  Q: {r['question']}")
    print()
    print(f"# After Opus generates JSON, run: python code/research/forward_predict_helper.py record --json '<json>'")


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
    else:
        # final sha256
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
