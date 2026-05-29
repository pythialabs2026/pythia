"""Step 20 — Window-size sensitivity for Step 19's non-monotone finding.

Step 19 used 14d windows at 15d strides and concluded Opus Brier is
non-monotone over time (deltas swing both signs). If the non-monotonicity
is a 14d artifact, smaller (7d) or larger (21d) windows might smooth it
into a monotone pattern.

Test: rerun the per-window paired Brier for window sizes ∈ {7, 14, 21}
days, non-overlapping stride. For each:
  - per-window brier_opus_paired, brier_market_paired, n_paired
  - inter-window delta signs (count +/- sign-flips)
  - monotonicity verdict (strictly_up / strictly_down / non_monotone)
  - max window-to-window jump

PASS finding "non-monotone is real" if 2 of 3 window sizes give
non-monotone. FAIL (i.e., 14d-artifact) if 7d and 21d both monotone.

Output: data/research/backtests/cutoff_clean_2026-05-29/leakage_curve_sensitivity.json
"""
from __future__ import annotations

import json
import math
import statistics
from datetime import datetime, timezone, timedelta
from pathlib import Path

REPO = Path("/home/ubuntu/pythia")
DIR = REPO / "data" / "research" / "backtests" / "cutoff_clean_2026-05-29"
COHORT = DIR / "cohort.jsonl"
PRED = DIR / "predictions.jsonl"
BASE = DIR / "baseline_prices.jsonl"
OUT = DIR / "leakage_curve_sensitivity.json"

WINDOW_SIZES = [7, 14, 21]
START = datetime(2026, 2, 1, tzinfo=timezone.utc)
END_CAP = datetime(2026, 5, 29, tzinfo=timezone.utc)  # data cutoff


def _parse(ts: str):
    if ts.endswith("+00"):
        ts = ts.replace("+00", "+00:00")
    if "T" in ts:
        return datetime.fromisoformat(ts.replace("Z", "+00:00"))
    return datetime.fromisoformat(ts).replace(tzinfo=timezone.utc)


def _per_window_brier(rows: list[dict], window_days: int) -> list[dict]:
    out = []
    cur = START
    while cur < END_CAP:
        nxt = cur + timedelta(days=window_days)
        bucket = [r for r in rows if cur <= r["closed_dt"] < nxt and r["p_market"] is not None]
        if len(bucket) >= 5:
            b_o = statistics.mean((r["p_opus"] - r["y"]) ** 2 for r in bucket)
            b_m = statistics.mean((r["p_market"] - r["y"]) ** 2 for r in bucket)
            out.append({
                "window_start": cur.date().isoformat(),
                "n_paired": len(bucket),
                "brier_opus_paired":   round(b_o, 6),
                "brier_market_paired": round(b_m, 6),
                "delta_opus_minus_market": round(b_o - b_m, 6),
            })
        cur = nxt
    return out


def _verdict(windows: list[dict]) -> dict:
    brs = [w["brier_opus_paired"] for w in windows]
    if len(brs) < 2:
        return {"verdict": "insufficient_windows", "n_windows": len(brs)}
    deltas = [brs[i+1] - brs[i] for i in range(len(brs)-1)]
    sign_flips = sum(
        1 for i in range(len(deltas)-1)
        if (deltas[i] > 0) != (deltas[i+1] > 0) and abs(deltas[i]) > 1e-6 and abs(deltas[i+1]) > 1e-6
    )
    monotone_up   = all(d >= -1e-6 for d in deltas)
    monotone_down = all(d <= 1e-6 for d in deltas)
    if monotone_up:
        verdict = "strictly_up"
    elif monotone_down:
        verdict = "strictly_down"
    else:
        verdict = "non_monotone"
    return {
        "n_windows": len(brs),
        "deltas":   [round(d, 6) for d in deltas],
        "n_sign_flips": sign_flips,
        "max_abs_jump": round(max(abs(d) for d in deltas), 6),
        "verdict": verdict,
    }


def main() -> int:
    cohort = {str(r["id"]): r for r in (json.loads(l) for l in COHORT.open())}
    preds = {str(r["market_id"]): r for r in (json.loads(l) for l in PRED.open())}
    base = {str(r["market_id"]): r for r in (json.loads(l) for l in BASE.open())}

    rows = []
    for mid, c in cohort.items():
        rows.append({
            "y": c["y"],
            "p_opus": preds[mid]["p_yes"],
            "p_market": base[mid]["market_p_at_freeze"],
            "closed_dt": _parse(c["closedTime"]),
        })

    per_size = {}
    for w in WINDOW_SIZES:
        windows = _per_window_brier(rows, w)
        verdict = _verdict(windows)
        per_size[str(w)] = {
            "window_days":   w,
            "stride_days":   w,
            "windows":       windows,
            "verdict_block": verdict,
        }

    verdicts = {s: v["verdict_block"].get("verdict") for s, v in per_size.items()}
    n_non_monotone = sum(1 for v in verdicts.values() if v == "non_monotone")

    if n_non_monotone >= 2:
        finding_robust = True
        interpretation = (
            f"Non-monotone pattern holds at {n_non_monotone}/3 window sizes — "
            "Step 19's finding is NOT a 14d artifact. Opus Brier degradation over time is "
            "genuinely cluster-driven, not a smooth cutoff-leakage gradient."
        )
    elif n_non_monotone == 1:
        finding_robust = False
        interpretation = (
            "Non-monotone only at 1/3 window sizes — the Step 19 finding is window-sensitive. "
            "Smaller/larger windows smooth it. Cluster-vs-gradient distinction is inconclusive."
        )
    else:
        finding_robust = False
        interpretation = (
            "ALL window sizes yield monotone Brier — Step 19's non-monotonicity was a 14d "
            "artifact. Reinstate the cutoff-leakage gradient hypothesis."
        )

    out = {
        "experiment": "cutoff_clean_2026-05-29",
        "analysis": "leakage_curve_sensitivity",
        "depends_on": "Step 19 leakage_curve.json",
        "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "window_sizes_tested": WINDOW_SIZES,
        "per_window_size": per_size,
        "verdicts_summary": verdicts,
        "n_non_monotone_of_3": n_non_monotone,
        "finding_robust": finding_robust,
        "interpretation": interpretation,
        "caveats": [
            "Stride = window_size (non-overlapping). Reduces redundant evidence vs Step 19's overlapping windows.",
            "Min n_paired per window = 5 — small windows may be omitted entirely.",
            "Brier is noisy at low n; 7d windows may legitimately appear monotone just from variance reduction.",
            "Even if robust, non-monotone ≠ no-leakage. Step 17 forward protocol remains the only true elimination.",
        ],
    }

    OUT.write_text(json.dumps(out, indent=2, ensure_ascii=False))
    print(json.dumps(out, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
