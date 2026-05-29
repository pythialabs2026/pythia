"""Step 19 — Fine-grained leakage curve.

Step 10 found Feb→Mar Brier asymmetry (opus 0.082→0.106 vs market 0.114→0.197).
Question: is this monotone decay (consistent with cutoff-leakage gradient) or
a regime break at one specific date?

Slice cohort by closedTime into rolling windows and plot per-window Brier:
  - window_start ∈ {2026-02-01, 2026-02-15, 2026-03-01, 2026-03-15, 2026-04-01, 2026-04-15, 2026-05-01}
  - each window: closedTime ∈ [ws, ws + 14d)
For each window compute brier_opus, brier_market, n_paired, mean_loss_diff.

Output: data/research/backtests/cutoff_clean_2026-05-29/leakage_curve.json
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
OUT = DIR / "leakage_curve.json"

WINDOWS = [
    "2026-02-01", "2026-02-15", "2026-03-01", "2026-03-15",
    "2026-04-01", "2026-04-15", "2026-05-01",
]
WINDOW_LEN_DAYS = 14


def _parse(ts: str):
    if ts.endswith("+00"):
        ts = ts.replace("+00", "+00:00")
    if "T" in ts:
        return datetime.fromisoformat(ts.replace("Z", "+00:00"))
    return datetime.fromisoformat(ts).replace(tzinfo=timezone.utc)


def _window_metrics(rows: list[dict]) -> dict:
    n = len(rows)
    if n == 0:
        return {"n_obs": 0}
    paired = [r for r in rows if r["p_market"] is not None]
    n_p = len(paired)
    brier_opus = statistics.mean((r["p_opus"] - r["y"]) ** 2 for r in rows)
    brier_opus_p = statistics.mean((r["p_opus"] - r["y"]) ** 2 for r in paired) if paired else None
    brier_mkt_p = statistics.mean((r["p_market"] - r["y"]) ** 2 for r in paired) if paired else None
    if n_p >= 2:
        d = [(r["p_opus"] - r["y"]) ** 2 - (r["p_market"] - r["y"]) ** 2 for r in paired]
        mu = statistics.mean(d); sd = statistics.stdev(d)
        se = sd / math.sqrt(n_p) if sd > 0 else None
        t = mu / se if se else None
        p_norm = math.erfc(abs(t) / math.sqrt(2.0)) if t is not None else None
    else:
        mu = t = p_norm = None
    return {
        "n_obs": n,
        "n_paired": n_p,
        "brier_opus":   round(brier_opus, 6),
        "brier_opus_paired":  round(brier_opus_p, 6) if brier_opus_p is not None else None,
        "brier_market_paired": round(brier_mkt_p, 6) if brier_mkt_p is not None else None,
        "delta_opus_minus_market": round((brier_opus_p - brier_mkt_p), 6) if (brier_opus_p is not None and brier_mkt_p is not None) else None,
        "mean_loss_diff": round(mu, 6) if mu is not None else None,
        "t_statistic":   round(t, 4) if t is not None else None,
        "p_two_sided_normal": round(p_norm, 6) if p_norm is not None else None,
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

    windows_out = []
    for ws in WINDOWS:
        start = datetime.fromisoformat(ws + "T00:00:00").replace(tzinfo=timezone.utc)
        end = start + timedelta(days=WINDOW_LEN_DAYS)
        bucket = [r for r in rows if start <= r["closed_dt"] < end]
        m = _window_metrics(bucket)
        m["window_start"] = ws
        m["window_end"]   = end.date().isoformat()
        m["window_len_days"] = WINDOW_LEN_DAYS
        windows_out.append(m)

    # Monotonicity check on brier_opus_paired across windows
    paired_briers = [(w["window_start"], w["brier_opus_paired"]) for w in windows_out if w.get("brier_opus_paired") is not None]
    if len(paired_briers) >= 2:
        deltas = [paired_briers[i+1][1] - paired_briers[i][1] for i in range(len(paired_briers)-1)]
        monotone_up = all(d >= 0 for d in deltas)
        monotone_down = all(d <= 0 for d in deltas)
        max_jump = max(abs(d) for d in deltas)
    else:
        deltas = []; monotone_up = monotone_down = None; max_jump = None

    market_briers = [(w["window_start"], w["brier_market_paired"]) for w in windows_out if w.get("brier_market_paired") is not None]
    if len(market_briers) >= 2:
        m_deltas = [market_briers[i+1][1] - market_briers[i][1] for i in range(len(market_briers)-1)]
    else:
        m_deltas = []

    interpretation = []
    if monotone_up:
        interpretation.append(
            "Opus Brier strictly increases as we move closer to today — "
            "consistent with monotone cutoff-leakage decay (more time post-cutoff → harder)."
        )
    elif monotone_down:
        interpretation.append("Opus Brier strictly decreases over time — anti-leakage pattern (unlikely).")
    else:
        interpretation.append(
            "Opus Brier is non-monotone over windows — degradation is event-driven or noisy, not a smooth gradient."
        )

    out = {
        "experiment": "cutoff_clean_2026-05-29",
        "analysis": "leakage_curve",
        "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "window_len_days": WINDOW_LEN_DAYS,
        "n_windows": len(WINDOWS),
        "windows": windows_out,
        "monotonicity": {
            "opus_paired_deltas": [round(d, 6) for d in deltas],
            "market_paired_deltas": [round(d, 6) for d in m_deltas],
            "opus_monotone_increasing": monotone_up,
            "opus_monotone_decreasing": monotone_down,
            "max_window_to_window_jump_opus": round(max_jump, 6) if max_jump is not None else None,
        },
        "interpretation": " ".join(interpretation),
        "caveats": [
            "Windows overlap with sub-cohorts from Step 10 — not orthogonal evidence.",
            "Window size (14d) and stride (15d) chosen ad hoc — sensitivity to choice not tested.",
            "Small windows (n_paired<10) have unstable Brier estimates.",
            "Non-monotone curve does not RULE OUT leakage — it just argues against smooth temporal gradient.",
        ],
    }

    OUT.write_text(json.dumps(out, indent=2, ensure_ascii=False))
    print(json.dumps(out, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
