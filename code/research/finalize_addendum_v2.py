"""Step 13 — Write summary_v2.json and append witness_v2 artifacts to freeze.json.

Original summary.json is IMMUTABLE — its sha256 must stay locked. We write
a sibling summary_v2.json that:
  - references the original by sha256
  - bundles Steps 9-12 results
  - lists pass/fail of the new diagnostic checks
  - is itself sha256-anchored into freeze.json as a v2 addendum

After this script runs: git commit + push, then a witness commit anchors
the new HEAD into freeze.json's git_witness_v2.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

REPO = Path("/home/ubuntu/pythia")
DIR = REPO / "data" / "research" / "backtests" / "cutoff_clean_2026-05-29"

SUMMARY = DIR / "summary.json"
SIG = DIR / "significance_test.json"
LEAK = DIR / "leakage_stress_test.json"
CAT = DIR / "category_breakdown.json"
KELLY = DIR / "kelly_sensitivity.json"

OUT = DIR / "summary_v2.json"
FREEZE = DIR / "freeze.json"


def _sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def main() -> int:
    sig = json.loads(SIG.read_text())
    leak = json.loads(LEAK.read_text())
    cat = json.loads(CAT.read_text())
    kelly = json.loads(KELLY.read_text())
    base = json.loads(SUMMARY.read_text())

    # Diagnostic pass/fail
    diag = {
        "brier_difference_significant_at_0_01": sig["dm_test"]["significant_at_0_01"],
        "edge_survives_march_buffer": (
            leak["sub_cohorts"]["mar_plus"]["p_two_sided_normal"] is not None
            and leak["sub_cohorts"]["mar_plus"]["p_two_sided_normal"] < 0.05
        ),
        "alpha_diversified_top_lt_90pct": (
            (cat["alpha_attribution"]["top_category_concentration"] or 0) < 0.90
        ),
        "kelly_5pct_recovers_locked_nav": kelly["reference_5pct_check"]["matches_locked_summary"],
    }
    n_pass = sum(1 for v in diag.values() if v)
    n_total = len(diag)

    v2 = {
        "experiment": "cutoff_clean_2026-05-29",
        "addendum_version": "v2",
        "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "original_summary": {
            "path": SUMMARY.relative_to(REPO).as_posix(),
            "sha256": _sha(SUMMARY),
            "n_pre_reg_pass": base["verdict"]["n_pass"],
            "n_pre_reg_total": base["verdict"]["n_total"],
            "all_pass": base["verdict"]["all_pass"],
        },
        "diagnostics": {
            "step_9_significance": {
                "sha256": _sha(SIG),
                "t_statistic": sig["dm_test"]["t_statistic"],
                "p_two_sided_normal": sig["dm_test"]["p_two_sided_normal"],
                "sign_test_p": sig["sign_test"]["p_two_sided_exact_binomial"],
                "direction": sig["dm_test"]["direction"],
                "mean_loss_diff_ci_95": sig["loss_differential"]["ci_95_normal"],
                "verdict": "Opus's lower Brier vs market is highly significant (p<10^-4).",
            },
            "step_10_leakage_stress": {
                "sha256": _sha(LEAK),
                "feb_only_brier_opus": leak["sub_cohorts"]["feb_only"]["brier_opus_all"],
                "mar_plus_brier_opus": leak["sub_cohorts"]["mar_plus"]["brier_opus_all"],
                "delta_opus":  leak["leak_diagnostic"]["delta_brier_opus_mar_minus_feb"],
                "delta_market": leak["leak_diagnostic"]["delta_brier_market_mar_minus_feb"],
                "mar_plus_t_stat": leak["sub_cohorts"]["mar_plus"]["t_statistic"],
                "mar_plus_p": leak["sub_cohorts"]["mar_plus"]["p_two_sided_normal"],
                "leak_signal": leak["leak_diagnostic"]["opus_only_degrades_in_march"],
                "verdict": (
                    "Asymmetric Feb→Mar degradation hints at partial leakage, "
                    "but the edge survives in mar_plus (t=-3.58, p=3.5e-4)."
                ),
            },
            "step_11_category_breakdown": {
                "sha256": _sha(CAT),
                "ranked_pnl": cat["alpha_attribution"]["ranked_by_net_pnl"],
                "top_category": cat["alpha_attribution"]["ranked_by_net_pnl"][0]["category"],
                "top_share": cat["alpha_attribution"]["top_category_concentration"],
                "verdict": (
                    "Sports dominates: 82.9% of net P&L from 178 sports bets. "
                    "tech_ai is net-negative (-$213). "
                    "True strategy = sports-flavored alpha."
                ),
            },
            "step_12_kelly_sensitivity": {
                "sha256": _sha(KELLY),
                "sweep_summary": [
                    {"cap": s["stake_cap"], "final_nav": s["final_nav"],
                     "sharpe": s["sharpe_daily"], "max_dd": s["max_drawdown"],
                     "bind_rate": s["cap_bind_rate"]}
                    for s in kelly["sweep"]
                ],
                "reference_check": kelly["reference_5pct_check"],
                "verdict": (
                    "5% cap binds 100% of bets. Sweep shows Sharpe ↓ and DD ↑ "
                    "as cap rises. 5% sits in a defensible Sharpe/DD compromise zone."
                ),
            },
        },
        "diagnostic_verdict": {
            "n_pass": n_pass,
            "n_total": n_total,
            "all_pass": n_pass == n_total,
            "details": diag,
        },
        "honest_caveats_v2": [
            "Leakage stress (Step 10): Feb Brier (0.082) lower than Mar+ (0.106) — possible Jan-cutoff leakage, but edge persists in Mar+ sub-cohort.",
            "Concentration risk (Step 11): 82.9% of P&L is from sports — not a 'general forecasting' alpha. Reframing as 'sports-bet adviser' would be more honest.",
            "Tech_AI category is net-negative — model is overconfident in its own domain (anti-pattern).",
            "Kelly sensitivity is in-sample: only sizing changes, same cohort and same prices. Out-of-sample behavior unknown.",
            "Sign test (n=219/299 opus wins) gives the most robust significance, free of magnitude assumptions.",
            "PAPER-ONLY remains. v2 addendum does NOT promote to Nostr/IPFS signed track.",
        ],
    }

    OUT.write_text(json.dumps(v2, indent=2, ensure_ascii=False))

    # Append v2 artifacts to freeze.json
    fz = json.loads(FREEZE.read_text())
    existing = {a["path"] for a in fz["artifacts"]}
    v2_artifacts = [
        ("significance_test_v2",  SIG),
        ("leakage_stress_v2",     LEAK),
        ("category_breakdown_v2", CAT),
        ("kelly_sensitivity_v2",  KELLY),
        ("summary_v2",            OUT),
    ]
    added = []
    for label, p in v2_artifacts:
        rel = p.relative_to(REPO).as_posix()
        if rel in existing:
            continue
        b = p.read_bytes()
        fz["artifacts"].append({
            "label": label,
            "path": rel,
            "sha256": hashlib.sha256(b).hexdigest(),
            "bytes": len(b),
        })
        added.append(rel)
    fz["addendum_v2_frozen_at_utc"] = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    fz["notes"].append(
        "Addendum v2 2026-05-29: significance_test + leakage_stress + category_breakdown + kelly_sensitivity + summary_v2 sealed."
    )
    FREEZE.write_text(json.dumps(fz, indent=2) + "\n")

    print(json.dumps(v2, indent=2, ensure_ascii=False))
    print()
    print(f"✅ summary_v2.json written ({len(json.dumps(v2))} bytes)")
    print(f"✅ freeze.json updated. v2 artifacts added: {len(added)}")
    for r in added:
        print(f"  + {r}")
    print(f"Total artifacts now: {len(fz['artifacts'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
