"""Forward paper Brier — ASCII visualization of summary.json.

Reads:  data/research/backtests/forward_paper_2026-05-28/summary.json
Emits:  3-line headline + side-by-side 10-bucket calibration plot (opus | market).

Calibration column shows:
  for each bucket [lo, hi): n markets, mean p, observed y_rate, |drift|, bar.

CLI:
  python3 code/research/calibration_plot.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

SUMMARY = Path("/home/ubuntu/pythia/data/research/backtests/forward_paper_2026-05-28/summary.json")


def _bar(p: float | None, y: float | None, width: int = 20) -> str:
    if p is None or y is None: return " " * width
    # mark p with '|' and y with 'o' on a 0..1 scale
    pos_p = max(0, min(width - 1, int(round(p * (width - 1)))))
    pos_y = max(0, min(width - 1, int(round(y * (width - 1)))))
    row = ["."] * width
    row[pos_p] = "|"
    if pos_y != pos_p:
        row[pos_y] = "o"
    else:
        row[pos_p] = "X"  # overlap
    return "".join(row)


def _drift(p: float | None, y: float | None) -> str:
    if p is None or y is None: return "    "
    return f"{abs(p - y):+.2f}".replace("+", " ")


def main() -> int:
    if not SUMMARY.exists():
        print("(no summary.json yet — first resolution hasn't happened)")
        print("Earliest endDate in cohort: 2026-06-01.")
        return 0
    s = json.loads(SUMMARY.read_text())
    n = s["n_resolved"]
    cov = s["completion_pct"]
    mb = s["mean_brier"]
    wr = s["opus_win_rate_vs_market"]

    print(f"=== Pythia Forward Paper — N={n} / 173 resolved ({cov:.1f}%) ===")
    print(f"  mean Brier  opus={mb['opus']:.4f}   market={mb['market']:.4f}   naive={mb['naive']:.4f}")
    edge = mb["market"] - mb["opus"]
    sign = "BETTER" if edge > 0 else ("WORSE " if edge < 0 else "TIED  ")
    print(f"  opus vs market: {sign} by {abs(edge):.4f}   opus_win_rate={wr*100:.1f}%")
    print()
    print("calibration  (| = mean predicted p,  o = observed y_rate,  X = overlap)")
    print(f"{'bucket':12s} {'n':>4s}  {'OPUS':<20s}  drift   {'MARKET':<20s}  drift")
    for bo, bm in zip(s["calibration_opus"], s["calibration_market"]):
        lo, hi = bo["lo"], bo["hi"]
        bucket = f"[{lo:.1f},{hi:.1f})"
        n_o = bo["n"]; n_m = bm["n"]
        # both opus and market buckets are over the same N markets but distributed differently
        bar_o = _bar(bo["p_mean"], bo["y_rate"])
        bar_m = _bar(bm["p_mean"], bm["y_rate"])
        d_o = _drift(bo["p_mean"], bo["y_rate"])
        d_m = _drift(bm["p_mean"], bm["y_rate"])
        print(f"{bucket:12s} O={n_o:>3d}  {bar_o}  {d_o}   {bar_m}  {d_m}   M={n_m}")
    print()
    print(f"witness commit  : {s.get('predictions_sha256','?')[:16]}…  (predictions)")
    print(f"resolutions sha : {s.get('resolutions_sha256','?')[:16]}…")
    return 0


if __name__ == "__main__":
    sys.exit(main())
