"""Step 10 — Leakage stress test.

The original cohort uses closedTime ≥ 2026-02-01 as a 1-month buffer after
Opus 4.7's January 2026 knowledge cutoff. If training data leaked into
February resolutions, restricting the cohort to closedTime ≥ 2026-03-01
(2-month buffer) should attenuate any apparent edge.

We recompute Brier metrics over the wider-buffer sub-cohort and compare
to the full cohort. If opus_brier improves OR stays unchanged in
March+ but degrades in Feb-only, that's consistent with leakage.

Output: significance_test sibling file, leakage_stress_test.json
"""
from __future__ import annotations

import json
import math
import statistics
from datetime import datetime, timezone
from pathlib import Path

REPO = Path("/home/ubuntu/pythia")
DIR = REPO / "data" / "research" / "backtests" / "cutoff_clean_2026-05-29"
BRIER = DIR / "brier_scores.jsonl"
OUT = DIR / "leakage_stress_test.json"


def _block_metrics(rows: list[dict]) -> dict:
    n = len(rows)
    paired = [r for r in rows if r["brier_market"] is not None]
    n_p = len(paired)
    mean_opus = statistics.mean(r["brier_opus"] for r in rows) if rows else None
    mean_naive = statistics.mean(r["brier_naive"] for r in rows) if rows else None
    mean_opus_p = statistics.mean(r["brier_opus"] for r in paired) if paired else None
    mean_mkt_p = statistics.mean(r["brier_market"] for r in paired) if paired else None
    opus_wins = sum(1 for r in paired if r["brier_opus"] < r["brier_market"]) if paired else 0

    # DM-style t-stat on the paired loss differential
    if n_p >= 2:
        d = [r["brier_opus"] - r["brier_market"] for r in paired]
        mu = statistics.mean(d)
        sd = statistics.stdev(d)
        se = sd / math.sqrt(n_p) if sd > 0 else None
        t = mu / se if se else None
        p_norm = math.erfc(abs(t) / math.sqrt(2.0)) if t is not None else None
    else:
        mu = sd = t = p_norm = None

    return {
        "n_total":            n,
        "n_paired":           n_p,
        "brier_opus_all":     round(mean_opus, 6) if mean_opus is not None else None,
        "brier_naive_all":    round(mean_naive, 6) if mean_naive is not None else None,
        "brier_opus_paired":  round(mean_opus_p, 6) if mean_opus_p is not None else None,
        "brier_market_paired":round(mean_mkt_p, 6) if mean_mkt_p is not None else None,
        "opus_win_rate":      round(opus_wins / n_p, 4) if n_p else None,
        "mean_loss_diff":     round(mu, 6) if mu is not None else None,
        "stdev_loss_diff":    round(sd, 6) if sd is not None else None,
        "t_statistic":        round(t, 4) if t is not None else None,
        "p_two_sided_normal": round(p_norm, 6) if p_norm is not None else None,
    }


def main() -> int:
    rows = [json.loads(l) for l in BRIER.open()]
    # closed_at is ISO: "2026-02-02T04:03:08+00:00"
    feb_only = [r for r in rows if r["closed_at"][:7] == "2026-02"]
    mar_plus = [r for r in rows if r["closed_at"][:10] >= "2026-03-01"]
    full = rows

    result = {
        "experiment": "cutoff_clean_2026-05-29",
        "analysis": "leakage_stress_test",
        "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "hypothesis": (
            "If Jan-cutoff leakage inflates Opus's edge in early-Feb resolutions, "
            "restricting cohort to closedTime ≥ 2026-03-01 should attenuate the edge."
        ),
        "sub_cohorts": {
            "full":      _block_metrics(full),
            "feb_only":  _block_metrics(feb_only),
            "mar_plus":  _block_metrics(mar_plus),
        },
    }

    f = result["sub_cohorts"]["feb_only"]
    m = result["sub_cohorts"]["mar_plus"]
    if f["brier_opus_all"] is not None and m["brier_opus_all"] is not None:
        delta_opus = m["brier_opus_all"] - f["brier_opus_all"]
        delta_mkt  = ((m["brier_market_paired"] or 0) - (f["brier_market_paired"] or 0))
        leak_signal = delta_opus > 0.02 and abs(delta_mkt) < abs(delta_opus) / 2
        result["leak_diagnostic"] = {
            "delta_brier_opus_mar_minus_feb":   round(delta_opus, 4),
            "delta_brier_market_mar_minus_feb": round(delta_mkt, 4),
            "opus_only_degrades_in_march":      leak_signal,
            "interpretation": (
                "Opus brier worsens by " + f"{delta_opus:+.4f}" + " from Feb→Mar; "
                + "market brier moves by " + f"{delta_mkt:+.4f}" + ". "
                + ("Asymmetric degradation suggests possible leakage." if leak_signal
                   else "No clear leakage signal — both sources move similarly.")
            ),
        }

    result["caveats"] = [
        "Feb sub-cohort dominated by sports (NBA/NFL/etc.) with short resolution windows; March+ skews different categories.",
        "Sub-cohort sizes differ (n_paired) — compare with that in mind.",
        "A null result does NOT prove no leakage; it just bounds detectable magnitude.",
    ]

    OUT.write_text(json.dumps(result, indent=2, ensure_ascii=False))
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
