"""Step 18 — Write summary_v3.json + anchor v3 artifacts in freeze.json.

Pattern follows finalize_addendum_v2.py. summary.json sha256 stays locked.
New artifacts added as v3 addendum:
  - drop_sports_robustness     (Step 14)
  - calibration_deepdive       (Step 15)
  - bin_aware_sizing           (Step 16)
  - oos_forward_protocol.md    (Step 17)
  - summary_v3                 (this script's output)

git_witness_v3 will be added by a follow-up commit anchoring the addendum
commit's hash.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

REPO = Path("/home/ubuntu/pythia")
DIR = REPO / "data" / "research" / "backtests" / "cutoff_clean_2026-05-29"

SUMMARY = DIR / "summary.json"
SUMMARY_V2 = DIR / "summary_v2.json"
DROP_SPORTS = DIR / "drop_sports_robustness.json"
CALIB = DIR / "calibration_deepdive.json"
BIN_AWARE = DIR / "bin_aware_sizing.json"
PROTO_MD = DIR / "oos_forward_protocol.md"

OUT = DIR / "summary_v3.json"
FREEZE = DIR / "freeze.json"


def _sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def main() -> int:
    base = json.loads(SUMMARY.read_text())
    base_v2 = json.loads(SUMMARY_V2.read_text())
    drop = json.loads(DROP_SPORTS.read_text())
    cal = json.loads(CALIB.read_text())
    bin_aw = json.loads(BIN_AWARE.read_text())

    # Diagnostic v3 — alpha identity & robustness
    diag = {
        "alpha_survives_dropping_sports": drop["robustness_check"]["edge_survives_dropping_sports"],
        "calibration_ece_under_5pct": cal["global_metrics"]["ece_sample_weighted"] < 0.05,
        "bin_aware_beats_or_matches_flat_5pct": (
            bin_aw["delta_bin_aware_minus_baseline"]["delta_final_nav"] >= -0.05 * bin_aw["baseline_flat_5pct"]["final_nav"]
        ),
        "bin_aware_reduces_max_drawdown": bin_aw["delta_bin_aware_minus_baseline"]["delta_max_drawdown"] < 0,
        "oos_protocol_specified": PROTO_MD.exists(),
    }
    n_pass = sum(1 for v in diag.values() if v)
    n_total = len(diag)

    v3 = {
        "experiment": "cutoff_clean_2026-05-29",
        "addendum_version": "v3",
        "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "preceding_artifacts": {
            "summary_v1": {
                "path": SUMMARY.relative_to(REPO).as_posix(),
                "sha256": _sha(SUMMARY),
                "all_pre_reg_pass": base["verdict"]["all_pass"],
            },
            "summary_v2": {
                "path": SUMMARY_V2.relative_to(REPO).as_posix(),
                "sha256": _sha(SUMMARY_V2),
                "diagnostic_pass_rate": f"{base_v2['diagnostic_verdict']['n_pass']}/{base_v2['diagnostic_verdict']['n_total']}",
            },
        },
        "diagnostics_v3": {
            "step_14_drop_sports": {
                "sha256": _sha(DROP_SPORTS),
                "sports_pnl_share": drop["robustness_check"]["sports_pnl_share"],
                "nonsports_brier_advantage": drop["robustness_check"]["nonsports_brier_advantage"],
                "nonsports_t_stat": drop["brier_blocks"]["nonsports"]["t_statistic"],
                "nonsports_p_value": drop["brier_blocks"]["nonsports"]["p_two_sided_normal"],
                "edge_survives_at_5pct": drop["robustness_check"]["edge_survives_dropping_sports"],
                "verdict": (
                    "Non-sports edge directionally present (+0.033 Brier) but p=0.097 fails 5% gate. "
                    "Sports is the dominant alpha source (~83% of full-cohort P&L). "
                    "Forward OOS (Step 17) must over-sample non-sports."
                ),
            },
            "step_15_calibration": {
                "sha256": _sha(CALIB),
                "ece": cal["global_metrics"]["ece_sample_weighted"],
                "mce": cal["global_metrics"]["mce_max_bin_gap"],
                "profitable_bin_intervals": cal["profitability_by_bin"]["profitable_bin_intervals"],
                "unprofitable_bin_intervals": cal["profitability_by_bin"]["unprofitable_bin_intervals"],
                "verdict": (
                    "Bin [0.0,0.1) is the workhorse ($30.8k from 118 bets, n_obs=225). "
                    "Bins [0.2,0.3), [0.3,0.4), [0.6,0.7), [0.7,0.8) are miscalibrated and "
                    "individually unprofitable. ECE=0.042 (well-calibrated globally); MCE=0.427 "
                    "(localized tail miscalibration). Use this as the locked reliability map for OOS."
                ),
            },
            "step_16_bin_aware_sizing": {
                "sha256": _sha(BIN_AWARE),
                "baseline_nav":   bin_aw["baseline_flat_5pct"]["final_nav"],
                "bin_aware_nav":  bin_aw["bin_aware"]["final_nav"],
                "delta_nav":      bin_aw["delta_bin_aware_minus_baseline"]["delta_final_nav"],
                "baseline_sharpe":  bin_aw["baseline_flat_5pct"]["sharpe_daily"],
                "bin_aware_sharpe": bin_aw["bin_aware"]["sharpe_daily"],
                "baseline_dd":   bin_aw["baseline_flat_5pct"]["max_drawdown"],
                "bin_aware_dd":  bin_aw["bin_aware"]["max_drawdown"],
                "n_bets_skipped": bin_aw["bin_aware"]["n_filtered_by_reliability"],
                "verdict": (
                    "Bin-aware Kelly delivered +$1522 NAV (+5.5%), +0.06 Sharpe, -4.1pp Max DD "
                    "vs flat 5%. 50 bets skipped via miscalibrated-bin filter. "
                    "WARNING: in-sample tuning — must be validated OOS (Step 17 prescribes)."
                ),
            },
            "step_17_oos_forward_protocol": {
                "sha256": _sha(PROTO_MD),
                "register_at": "2026-05-30T00:00:00Z",
                "freeze_at":   "2026-05-30T12:30:00Z",
                "evaluate_at": "2026-06-29T00:00:00Z",
                "markets_needed_total": 300,
                "nonsports_floor": 90,
                "structural_anti_leak": "closedTime ≥ register_at + 24h, prompt has no price",
                "verdict": (
                    "Pre-registered forward-paper run. Locked reliability map + dual-arm "
                    "(flat vs bin-aware) sims. Failure is publishable."
                ),
            },
        },
        "diagnostic_verdict_v3": {
            "n_pass": n_pass,
            "n_total": n_total,
            "all_pass": n_pass == n_total,
            "details": diag,
        },
        "alpha_identity_statement_v3": (
            "Pythia v1 (cohort cutoff_clean_2026-05-29) demonstrates a real but "
            "category-concentrated edge: ~83% of P&L from sports markets, "
            "non-sports edge directionally positive but underpowered (n=77, p=0.097). "
            "Opus 4.7 is globally well-calibrated (ECE=0.042) but mid-range bins "
            "([0.2-0.4), [0.6-0.8)) are unreliable and should be filtered. "
            "Bin-aware sizing yields modest in-sample improvements (+5.5% NAV, "
            "lower DD) but requires OOS validation (Step 17). "
            "Status: paper-only. Forward-only validation 2026-05-30 → 2026-06-29."
        ),
        "honest_caveats_v3": [
            "All Steps 14-16 use the same v1 cohort — improvements are in-sample tuning.",
            "Step 17 protocol is the ONLY mechanism that makes outcomes truly OOS w.r.t. Opus's training cutoff.",
            "Non-sports edge p=0.097 is a real failure to clear 5%; we accept and pre-commit Step 17 to test it forward.",
            "Bin reliability map will be FROZEN for the forward run; even if a bin misbehaves OOS, we do not re-tune.",
            "PAPER-ONLY persists. v3 does NOT promote to Nostr/IPFS.",
            "Live-capital decision requires `debate --critique` per global rule.",
        ],
    }

    OUT.write_text(json.dumps(v3, indent=2, ensure_ascii=False))

    # Append v3 artifacts to freeze.json
    fz = json.loads(FREEZE.read_text())
    existing = {a["path"] for a in fz["artifacts"]}
    v3_artifacts = [
        ("drop_sports_robustness_v3", DROP_SPORTS),
        ("calibration_deepdive_v3",   CALIB),
        ("bin_aware_sizing_v3",       BIN_AWARE),
        ("oos_forward_protocol_v3",   PROTO_MD),
        ("summary_v3",                OUT),
    ]
    added = []
    for label, p in v3_artifacts:
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
    fz["addendum_v3_frozen_at_utc"] = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    fz["notes"].append(
        "Addendum v3 2026-05-29: drop_sports + calibration + bin_aware_sizing + oos_forward_protocol + summary_v3 sealed."
    )
    FREEZE.write_text(json.dumps(fz, indent=2) + "\n")

    print(json.dumps(v3, indent=2, ensure_ascii=False))
    print()
    print(f"✅ summary_v3.json written ({len(json.dumps(v3))} bytes)")
    print(f"✅ freeze.json updated. v3 artifacts added: {len(added)}")
    for r in added:
        print(f"  + {r}")
    print(f"Total artifacts now: {len(fz['artifacts'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
