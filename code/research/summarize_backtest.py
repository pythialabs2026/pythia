"""Aggregate cutoff-clean backtest results into summary.json + ASCII calibration plot.

Inputs:
  brier_scores.jsonl, pnl_log.jsonl, daily_nav.jsonl

Outputs:
  summary.json          (machine-readable metrics + pre-reg pass/fail)
  calibration.txt       (ASCII calibration plot)

Pre-registered success criteria (from methodology.md):
  Brier track:
    - N resolved ≥ 50
    - mean(brier_opus) < mean(brier_market)
    - opus_win_rate_vs_market > 0.55
    - max bucket calibration drift < 0.15
  Backtest-specific:
    - final NAV > $1020
    - daily Sharpe > 1.0
    - max drawdown < 30%
"""
from __future__ import annotations

import hashlib
import json
import math
import statistics
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

REPO = Path("/home/ubuntu/pythia")
DIR = REPO / "data" / "research" / "backtests" / "cutoff_clean_2026-05-29"

BANKROLL_0 = 1000.0


def _sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def _load(p: Path) -> list[dict]:
    return [json.loads(l) for l in p.open()]


def main() -> int:
    brier = _load(DIR / "brier_scores.jsonl")
    pnl = _load(DIR / "pnl_log.jsonl")
    nav = _load(DIR / "daily_nav.jsonl")

    n_total = len(brier)
    paired = [r for r in brier if r["brier_market"] is not None]
    n_paired = len(paired)

    mean_brier_opus_all = statistics.mean(r["brier_opus"] for r in brier)
    mean_brier_naive_all = statistics.mean(r["brier_naive"] for r in brier)
    mean_brier_opus_paired = statistics.mean(r["brier_opus"] for r in paired)
    mean_brier_market_paired = statistics.mean(r["brier_market"] for r in paired)
    opus_wins = sum(1 for r in paired if r["brier_opus"] < r["brier_market"])
    opus_win_rate = opus_wins / n_paired if n_paired else 0.0

    # Calibration buckets (all 390)
    buckets = [[] for _ in range(10)]
    for r in brier:
        idx = min(int(r["p_opus"] * 10), 9)
        buckets[idx].append((r["p_opus"], r["y"]))
    cal = []
    drifts_all = []
    drifts_meaningful = []  # n >= 10 only
    for i, bucket in enumerate(buckets):
        edges = (i / 10, (i + 1) / 10)
        if not bucket:
            cal.append({"bin_lo": edges[0], "bin_hi": edges[1], "n": 0,
                        "p_mean": None, "y_rate": None, "drift": None})
            continue
        p_mean = statistics.mean(p for p, _ in bucket)
        y_rate = statistics.mean(y for _, y in bucket)
        drift = abs(p_mean - y_rate)
        cal.append({"bin_lo": edges[0], "bin_hi": edges[1], "n": len(bucket),
                    "p_mean": round(p_mean, 4), "y_rate": round(y_rate, 4),
                    "drift": round(drift, 4)})
        drifts_all.append(drift)
        if len(bucket) >= 10:
            drifts_meaningful.append(drift)
    max_drift_all = max(drifts_all) if drifts_all else None
    max_drift_meaningful = max(drifts_meaningful) if drifts_meaningful else None

    # Monthly Brier (leakage diagnostic)
    by_month = defaultdict(list)
    for r in brier:
        by_month[r["closed_at"][:7]].append(r["brier_opus"])
    monthly = {m: {"n": len(v), "mean_brier_opus": round(statistics.mean(v), 4)}
               for m, v in sorted(by_month.items())}

    # P&L
    n_bets = len(pnl)
    n_winners = sum(1 for p in pnl if p["net_pnl"] > 0)
    final_nav = pnl[-1]["bankroll_after"] if pnl else BANKROLL_0
    roi = final_nav / BANKROLL_0 - 1.0
    capped = sum(1 for p in pnl if abs(p["stake_frac"] - 0.05) < 1e-9)

    # Sharpe + max DD
    sharpe = None; max_dd = 0.0
    if nav:
        navs = [BANKROLL_0] + [d["nav"] for d in nav]
        rets = [navs[i] / navs[i-1] - 1.0 for i in range(1, len(navs)) if navs[i-1] > 0]
        if len(rets) >= 2:
            mu = statistics.mean(rets)
            sd = statistics.stdev(rets)
            if sd > 0:
                sharpe = round(mu / sd * math.sqrt(365), 4)
        peak = navs[0]
        for v in navs:
            peak = max(peak, v)
            dd = (peak - v) / peak if peak > 0 else 0.0
            max_dd = max(max_dd, dd)
        max_dd = round(max_dd, 4)

    # Pre-registered pass/fail
    pre_reg = {
        "n_resolved_ge_50":              {"threshold": 50,    "actual": n_total,                 "pass": n_total >= 50},
        "opus_brier_lt_market":          {"threshold": None,  "actual": [round(mean_brier_opus_paired, 4), round(mean_brier_market_paired, 4)],
                                           "pass": mean_brier_opus_paired < mean_brier_market_paired},
        "opus_win_rate_gt_0_55":         {"threshold": 0.55,  "actual": round(opus_win_rate, 4), "pass": opus_win_rate > 0.55},
        "max_bucket_drift_lt_0_15":      {"threshold": 0.15,  "actual": round(max_drift_all, 4) if max_drift_all is not None else None,
                                           "pass": (max_drift_all is not None) and max_drift_all < 0.15},
        "max_bucket_drift_n10_lt_0_15":  {"threshold": 0.15,  "actual": round(max_drift_meaningful, 4) if max_drift_meaningful is not None else None,
                                           "pass": (max_drift_meaningful is not None) and max_drift_meaningful < 0.15,
                                           "note": "buckets with n>=10 only — robustness check"},
        "final_nav_gt_1020":             {"threshold": 1020,  "actual": round(final_nav, 2),     "pass": final_nav > 1020},
        "sharpe_daily_gt_1_0":           {"threshold": 1.0,   "actual": sharpe,                  "pass": sharpe is not None and sharpe > 1.0},
        "max_dd_lt_0_30":                {"threshold": 0.30,  "actual": max_dd,                  "pass": max_dd < 0.30},
    }

    summary = {
        "experiment": "cutoff_clean_2026-05-29",
        "track": "paper-only",
        "model": "claude-opus-4-7",
        "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "n_markets_total": n_total,
        "n_markets_paired_with_market_price": n_paired,
        "brier": {
            "all_markets": {
                "opus":  round(mean_brier_opus_all, 4),
                "naive": round(mean_brier_naive_all, 4),
            },
            "paired_with_market": {
                "opus":   round(mean_brier_opus_paired, 4),
                "market": round(mean_brier_market_paired, 4),
                "opus_win_rate_vs_market": round(opus_win_rate, 4),
                "opus_wins": opus_wins,
                "n": n_paired,
            },
        },
        "calibration": {
            "buckets": cal,
            "max_drift_any": round(max_drift_all, 4) if max_drift_all is not None else None,
            "max_drift_n_ge_10": round(max_drift_meaningful, 4) if max_drift_meaningful is not None else None,
        },
        "monthly_brier_opus": monthly,
        "pnl": {
            "bankroll_start": BANKROLL_0,
            "bankroll_end":   round(final_nav, 4),
            "roi":            round(roi, 6),
            "n_bets":         n_bets,
            "n_winners":      n_winners,
            "win_rate":       round(n_winners / n_bets, 4) if n_bets else None,
            "kelly_cap_bind_rate": round(capped / n_bets, 4) if n_bets else None,
            "sharpe_daily":   sharpe,
            "max_drawdown":   max_dd,
        },
        "pre_registered_criteria": pre_reg,
        "verdict": {
            "all_pass": all(c["pass"] for c in pre_reg.values()),
            "n_pass":   sum(1 for c in pre_reg.values() if c["pass"]),
            "n_total":  len(pre_reg),
        },
        "honest_caveats": [
            "Kelly cap binds 100% of bets — effective sizing is flat 5%, not Kelly.",
            "Calibration drift fails the 0.15 threshold; concentrated in mid-confidence (0.2–0.4) and sparse (n<5) buckets.",
            f"Monthly Opus Brier: Feb {monthly.get('2026-02', {}).get('mean_brier_opus', '?')} vs Apr {monthly.get('2026-04', {}).get('mean_brier_opus', '?')} — possible Jan-cutoff leakage into Feb resolutions.",
            "Markets with baseline=null (n=91, mostly low-vol) excluded from market comparison and P&L.",
            "Friction model (2% fee + 1% slippage) is conservative-optimistic — real Polymarket execution is worse in thin markets.",
            "Survivorship: cohort = only resolved markets. Disputed/refunded markets invisible.",
            "PAPER-ONLY: not a signed Pythia prediction. NOT promoted to Nostr/IPFS track.",
        ],
        "artifacts_sha256": {
            "cohort":             _sha(DIR / "cohort.jsonl"),
            "predictions":        _sha(DIR / "predictions.jsonl"),
            "baseline_prices":    _sha(DIR / "baseline_prices.jsonl"),
            "brier_scores":       _sha(DIR / "brier_scores.jsonl"),
            "pnl_log":            _sha(DIR / "pnl_log.jsonl"),
            "daily_nav":          _sha(DIR / "daily_nav.jsonl"),
        },
    }

    (DIR / "summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False))

    # ASCII calibration plot
    lines = []
    lines.append(f"Cutoff-Clean Backtest 2026-05-29 — Calibration Plot")
    lines.append(f"N={n_total}  (paired w/ market = {n_paired})")
    lines.append("=" * 64)
    lines.append("")
    lines.append("Bucket           n   p̄     ȳ     drift  |  p̄ vs ȳ (◊=p̄, ●=ȳ)")
    lines.append("-" * 64)
    for c in cal:
        bn = f"[{c['bin_lo']:.1f},{c['bin_hi']:.1f})"
        if c["n"] == 0:
            lines.append(f"{bn:14s}  n=0")
            continue
        p_pos = int(round(c["p_mean"] * 30))
        y_pos = int(round(c["y_rate"] * 30))
        bar = [" "] * 31
        bar[p_pos] = "◊"
        if y_pos != p_pos:
            bar[y_pos] = "●"
        else:
            bar[p_pos] = "✦"
        bar_s = "".join(bar)
        lines.append(f"{bn:14s} {c['n']:>3d}  {c['p_mean']:.3f} {c['y_rate']:.3f}  {c['drift']:.3f}  |{bar_s}|")
    lines.append("-" * 64)
    lines.append("Perfect calibration: ◊ and ● overlap (✦). Drift = |p̄ - ȳ|.")
    lines.append(f"Max drift (any n): {max_drift_all:.3f}  |  Max drift (n≥10): {max_drift_meaningful:.3f}")
    lines.append("")
    lines.append(f"[Brier] opus_all={mean_brier_opus_all:.4f}  naive={mean_brier_naive_all:.4f}")
    lines.append(f"[Brier paired] opus={mean_brier_opus_paired:.4f}  market={mean_brier_market_paired:.4f}  opus_win_rate={opus_win_rate*100:.1f}%")
    lines.append(f"[P&L] start=$1000  end=${final_nav:,.2f}  ROI={roi*100:+.2f}%  Sharpe={sharpe}  maxDD={max_dd*100:.2f}%")
    lines.append("")
    lines.append("Pre-registered criteria:")
    for k, v in pre_reg.items():
        ok = "✅" if v["pass"] else "❌"
        lines.append(f"  {ok} {k:34s}  actual={v['actual']}")
    lines.append("")
    lines.append("Honest caveats:")
    for c in summary["honest_caveats"]:
        lines.append(f"  - {c}")

    plot = "\n".join(lines)
    (DIR / "calibration.txt").write_text(plot)
    print(plot)
    print()
    print(f"✅ summary.json written ({len(json.dumps(summary))} bytes)")
    print(f"✅ calibration.txt written")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
