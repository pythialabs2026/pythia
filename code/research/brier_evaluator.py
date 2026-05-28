"""Forward paper Brier evaluator.

predictions.jsonl × resolutions.jsonl → 3-way Brier:
  opus    : Opus 4.7 P(YES)
  market  : market_p_yes_at_freeze (cohort.jsonl 기록, prediction에는 미노출)
  naive   : 0.5 일정 baseline

Brier = (p - y)^2.   낮을수록 좋음.

Output:
  data/research/backtests/forward_paper_2026-05-28/
    brier_scores.jsonl   ← per-market: market_id, y, p_opus, p_market, brier_*
    summary.json         ← N, mean Brier 3-way, calibration buckets, win-rate vs market

CLI:
  python3 code/research/brier_evaluator.py
"""
from __future__ import annotations

import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO = Path("/home/ubuntu/pythia")
COHORT_DIR = REPO / "data" / "research" / "backtests" / "forward_paper_2026-05-28"
COHORT = COHORT_DIR / "cohort.jsonl"
PRED = COHORT_DIR / "predictions.jsonl"
RES = COHORT_DIR / "resolutions.jsonl"
BRIER = COHORT_DIR / "brier_scores.jsonl"
SUMMARY = COHORT_DIR / "summary.json"


def _load_jsonl(p: Path) -> list[dict]:
    if not p.exists(): return []
    return [json.loads(l) for l in p.open() if l.strip()]


def _calibration(rows: list[dict], p_key: str) -> list[dict]:
    """10개 균등 bucket [0.0,0.1)...[0.9,1.0]. y_rate 평균."""
    buckets = [{"lo": i/10, "hi": (i+1)/10, "n": 0, "p_sum": 0.0, "y_sum": 0} for i in range(10)]
    for r in rows:
        p = r[p_key]
        idx = min(int(p * 10), 9)
        buckets[idx]["n"] += 1
        buckets[idx]["p_sum"] += p
        buckets[idx]["y_sum"] += r["y"]
    for b in buckets:
        if b["n"]:
            b["p_mean"] = round(b["p_sum"] / b["n"], 4)
            b["y_rate"] = round(b["y_sum"] / b["n"], 4)
        else:
            b["p_mean"] = None; b["y_rate"] = None
        del b["p_sum"]
    return buckets


def main() -> int:
    cohort = {r["id"]: r for r in _load_jsonl(COHORT)}
    preds = {p["market_id"]: p for p in _load_jsonl(PRED)}
    resolutions = _load_jsonl(RES)

    resolved = [r for r in resolutions if r["status"] == "resolved"]
    print(f"cohort      : {len(cohort)}")
    print(f"predictions : {len(preds)}")
    print(f"resolutions : {len(resolutions)} (resolved={len(resolved)})")

    if not resolved:
        print("\n(no resolved markets yet — exiting without writing brier_scores.jsonl)")
        return 0

    rows = []
    for r in resolved:
        mid = r["market_id"]
        if mid not in preds or mid not in cohort:
            continue
        y = r["y"]
        p_opus = preds[mid]["p_yes"]
        p_market = cohort[mid]["market_p_yes_at_freeze"]
        p_naive = 0.5
        rows.append({
            "market_id": mid,
            "slug": preds[mid]["slug"],
            "y": y,
            "p_opus": p_opus,
            "p_market": p_market,
            "p_naive": p_naive,
            "brier_opus": round((p_opus - y) ** 2, 6),
            "brier_market": round((p_market - y) ** 2, 6),
            "brier_naive": round((p_naive - y) ** 2, 6),
            "opus_better_than_market": (p_opus - y) ** 2 < (p_market - y) ** 2,
        })

    if not rows:
        print("\n(resolutions present but no overlap with predictions — schema mismatch?)")
        return 1

    with BRIER.open("w") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False, separators=(",", ":")) + "\n")

    n = len(rows)
    mean = lambda k: round(sum(r[k] for r in rows) / n, 6)
    win_rate_vs_market = round(sum(1 for r in rows if r["opus_better_than_market"]) / n, 4)

    summary = {
        "schema": "pythia.forward_paper_brier.v0",
        "evaluated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "n_resolved": n,
        "n_cohort": len(cohort),
        "completion_pct": round(100 * n / len(cohort), 2),
        "mean_brier": {
            "opus": mean("brier_opus"),
            "market": mean("brier_market"),
            "naive": mean("brier_naive"),
        },
        "opus_win_rate_vs_market": win_rate_vs_market,
        "calibration_opus": _calibration(rows, "p_opus"),
        "calibration_market": _calibration(rows, "p_market"),
        "predictions_sha256": hashlib.sha256(PRED.read_bytes()).hexdigest(),
        "cohort_sha256": hashlib.sha256(COHORT.read_bytes()).hexdigest(),
        "resolutions_sha256": hashlib.sha256(RES.read_bytes()).hexdigest(),
        "brier_scores_sha256": hashlib.sha256(BRIER.read_bytes()).hexdigest(),
        "track": "paper-only",
    }
    SUMMARY.write_text(json.dumps(summary, ensure_ascii=False, indent=2))

    print(f"\n=== Brier (n={n}, {summary['completion_pct']:.1f}% of cohort) ===")
    print(f"  opus   : {summary['mean_brier']['opus']:.4f}")
    print(f"  market : {summary['mean_brier']['market']:.4f}")
    print(f"  naive  : {summary['mean_brier']['naive']:.4f}")
    print(f"  opus win-rate vs market: {win_rate_vs_market*100:.1f}%")
    print(f"\nwritten: {BRIER.relative_to(REPO)}")
    print(f"written: {SUMMARY.relative_to(REPO)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
