"""Step 15 — Calibration deep-dive.

Bin Opus's predicted p_yes into 10 buckets [0.0,0.1), [0.1,0.2), …,
[0.9,1.0]. For each bin compute:
  - reliability:  actual outcome rate (mean y), the empirical truth
  - mean_predicted: mean Opus p_yes inside the bin
  - calibration_gap: predicted − actual (positive = overconfident YES)
  - n_obs: cohort markets falling in bin
  - mean_market_prior: mean baseline market p at freeze
  - n_bets: markets where |Opus − Market| ≥ 5% edge threshold
  - bet_win_rate: P&L wins / n_bets in this bin
  - mean_net_pnl: average bet P&L

Output: data/research/backtests/cutoff_clean_2026-05-29/calibration_deepdive.json
"""
from __future__ import annotations

import json
import math
import statistics
from datetime import datetime, timezone
from pathlib import Path

REPO = Path("/home/ubuntu/pythia")
DIR = REPO / "data" / "research" / "backtests" / "cutoff_clean_2026-05-29"
COHORT = DIR / "cohort.jsonl"
PRED = DIR / "predictions.jsonl"
BASE = DIR / "baseline_prices.jsonl"
PNL = DIR / "pnl_log.jsonl"
OUT = DIR / "calibration_deepdive.json"

BINS = [(i / 10.0, (i + 1) / 10.0) for i in range(10)]  # [0.0,0.1)…[0.9,1.0]


def _bin_idx(p: float) -> int:
    if p >= 1.0:
        return 9
    if p < 0.0:
        return 0
    return int(p * 10)


def _ev_naive(p_opus: float, p_market: float) -> float:
    """Expected return per $1 staked, ignoring fees/slip.

    Bet on YES if Opus > Market: profit = (1 - p_market) if win, -1 if lose.
    Win prob is Opus's p_yes (his belief).
    E[ret] = p_opus*(1/p_market - 1) - (1-p_opus)*1
    Similarly for NO.
    """
    edge = p_opus - p_market
    if abs(edge) < 1e-9 or p_market <= 0 or p_market >= 1:
        return 0.0
    if edge > 0:
        return p_opus * (1.0 / p_market - 1.0) - (1.0 - p_opus) * 1.0
    else:
        return (1.0 - p_opus) * (1.0 / (1.0 - p_market) - 1.0) - p_opus * 1.0


def main() -> int:
    cohort = {str(r["id"]): r for r in (json.loads(l) for l in COHORT.open())}
    preds = {str(r["market_id"]): r for r in (json.loads(l) for l in PRED.open())}
    base = {str(r["market_id"]): r for r in (json.loads(l) for l in BASE.open())}
    pnl_rows = [json.loads(l) for l in PNL.open()]
    pnl_by_mid = {str(r["market_id"]): r for r in pnl_rows}

    bin_data: list[dict] = [
        {"lo": lo, "hi": hi, "rows": []} for lo, hi in BINS
    ]

    for mid, c in cohort.items():
        p_opus = preds[mid]["p_yes"]
        p_market = base[mid].get("market_p_at_freeze")
        y = c["y"]
        idx = _bin_idx(p_opus)
        ev = _ev_naive(p_opus, p_market) if p_market is not None else None
        bin_data[idx]["rows"].append({
            "mid": mid,
            "p_opus": p_opus,
            "p_market": p_market,
            "y": y,
            "ev": ev,
            "bet": pnl_by_mid.get(mid),  # None if no bet placed
        })

    bins_out = []
    overall_n = 0
    overall_brier = 0.0
    overall_brier_sum_paired = 0.0
    overall_n_paired = 0
    for i, b in enumerate(bin_data):
        rows = b["rows"]
        n = len(rows)
        if n == 0:
            bins_out.append({
                "bin_idx": i,
                "interval": [b["lo"], b["hi"]],
                "n_obs": 0,
            })
            continue
        mean_pred = statistics.mean(r["p_opus"] for r in rows)
        actual_rate = statistics.mean(r["y"] for r in rows)
        gap = mean_pred - actual_rate
        paired_rows = [r for r in rows if r["p_market"] is not None]
        n_paired = len(paired_rows)
        mean_market = (statistics.mean(r["p_market"] for r in paired_rows)
                       if paired_rows else None)
        # Bets in bin
        bet_rows = [r for r in rows if r["bet"] is not None]
        n_bets = len(bet_rows)
        n_winners = sum(1 for r in bet_rows if r["bet"]["net_pnl"] > 0)
        mean_pnl = (statistics.mean(r["bet"]["net_pnl"] for r in bet_rows)
                    if bet_rows else None)
        total_pnl = (sum(r["bet"]["net_pnl"] for r in bet_rows)
                     if bet_rows else 0.0)
        mean_ev = (statistics.mean(r["ev"] for r in paired_rows if r["ev"] is not None)
                   if paired_rows else None)
        # Brier per bin
        brier_opus = statistics.mean((r["p_opus"] - r["y"]) ** 2 for r in rows)
        brier_market = (statistics.mean((r["p_market"] - r["y"]) ** 2 for r in paired_rows)
                        if paired_rows else None)
        bins_out.append({
            "bin_idx": i,
            "interval": [round(b["lo"], 2), round(b["hi"], 2)],
            "n_obs": n,
            "n_paired": n_paired,
            "mean_predicted_opus": round(mean_pred, 4),
            "actual_win_rate": round(actual_rate, 4),
            "calibration_gap": round(gap, 4),
            "mean_market_prior": round(mean_market, 4) if mean_market is not None else None,
            "n_bets_placed": n_bets,
            "n_bet_winners": n_winners,
            "bet_win_rate": round(n_winners / n_bets, 4) if n_bets else None,
            "mean_bet_net_pnl": round(mean_pnl, 4) if mean_pnl is not None else None,
            "total_bet_net_pnl": round(total_pnl, 2),
            "mean_ev_naive": round(mean_ev, 4) if mean_ev is not None else None,
            "brier_opus": round(brier_opus, 6),
            "brier_market": round(brier_market, 6) if brier_market is not None else None,
        })
        overall_n += n
        overall_brier += brier_opus * n
        if paired_rows:
            overall_brier_sum_paired += (brier_market or 0.0) * n_paired
            overall_n_paired += n_paired

    # ECE — expected calibration error (sample-weighted absolute gap)
    ece = sum(
        b["n_obs"] * abs(b["calibration_gap"])
        for b in bins_out if b.get("n_obs", 0) > 0
    ) / overall_n if overall_n else None

    # MCE — maximum calibration error
    mce = max(
        (abs(b["calibration_gap"]) for b in bins_out if b.get("n_obs", 0) > 0),
        default=None,
    )

    # Reliability classification per bin (gap < 0.05 = well-calibrated, < 0.15 = ok, else miscalibrated)
    for b in bins_out:
        if b.get("n_obs", 0) == 0:
            continue
        g = abs(b["calibration_gap"])
        if g < 0.05:
            b["calibration_quality"] = "well_calibrated"
        elif g < 0.15:
            b["calibration_quality"] = "moderately_calibrated"
        else:
            b["calibration_quality"] = "miscalibrated"

    # Profitable bins
    profitable_bins = [b for b in bins_out
                       if b.get("total_bet_net_pnl") is not None
                       and b["total_bet_net_pnl"] > 0
                       and b.get("n_bets_placed", 0) >= 3]
    unprofitable_bins = [b for b in bins_out
                         if b.get("total_bet_net_pnl") is not None
                         and b["total_bet_net_pnl"] <= 0
                         and b.get("n_bets_placed", 0) >= 3]

    out = {
        "experiment": "cutoff_clean_2026-05-29",
        "analysis": "calibration_deepdive",
        "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "n_total": overall_n,
        "bin_count": len(BINS),
        "bins": bins_out,
        "global_metrics": {
            "ece_sample_weighted": round(ece, 6) if ece is not None else None,
            "mce_max_bin_gap":     round(mce, 4) if mce is not None else None,
            "n_paired_total":      overall_n_paired,
        },
        "profitability_by_bin": {
            "profitable_bin_intervals":   [b["interval"] for b in profitable_bins],
            "unprofitable_bin_intervals": [b["interval"] for b in unprofitable_bins],
        },
        "caveats": [
            "Small-bin reliability is high-variance — bins with n_obs<10 should not drive sizing.",
            "Tail bins (0.0-0.1 and 0.9-1.0) often have few observations because Opus rarely places extreme probabilities.",
            "ECE conflates over- and under-confidence; check sign of calibration_gap per bin separately.",
            "EV is naive (no fees/slip); P&L is realized with 2% fee + 1% slip; they should agree directionally but differ in magnitude.",
        ],
        "interpretation_hint": (
            "Use this to inform Step 16 (bin-aware sizing): scale Kelly stake by "
            "max(0, 1 - |calibration_gap|/0.1) so well-calibrated bins get full Kelly "
            "and miscalibrated bins get heavily discounted stakes."
        ),
    }

    OUT.write_text(json.dumps(out, indent=2, ensure_ascii=False))
    print(json.dumps(out, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
