"""Step 21 — Write summary_v4.json + anchor v4 artifacts in freeze.json.

Pattern follows finalize_addendum_v3.py. Prior summary sha256s stay locked.
New artifacts added as v4 addendum:
  - leakage_curve              (Step 19)
  - leakage_curve_sensitivity  (Step 20)
  - summary_v4                 (this script's output)

git_witness_v4 will be added by a follow-up commit anchoring the addendum
commit's hash.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

REPO = Path("/home/ubuntu/pythia")
DIR = REPO / "data" / "research" / "backtests" / "cutoff_clean_2026-05-29"

SUMMARY    = DIR / "summary.json"
SUMMARY_V2 = DIR / "summary_v2.json"
SUMMARY_V3 = DIR / "summary_v3.json"
LEAKAGE    = DIR / "leakage_curve.json"
SENS       = DIR / "leakage_curve_sensitivity.json"

OUT    = DIR / "summary_v4.json"
FREEZE = DIR / "freeze.json"


def _sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def main() -> int:
    base    = json.loads(SUMMARY.read_text())
    base_v2 = json.loads(SUMMARY_V2.read_text())
    base_v3 = json.loads(SUMMARY_V3.read_text())
    leak    = json.loads(LEAKAGE.read_text())
    sens    = json.loads(SENS.read_text())

    # Diagnostic v4 — leakage-gradient discrimination
    diag = {
        "step19_yielded_at_least_5_windows":
            len([w for w in leak["windows"] if w.get("brier_opus_paired") is not None]) >= 5,
        "step19_opus_brier_non_monotone": (
            leak["monotonicity"]["opus_monotone_increasing"] is False
            and leak["monotonicity"]["opus_monotone_decreasing"] is False
        ),
        "step20_finding_robust_to_window_size": sens["finding_robust"],
        "step20_n_non_monotone_at_least_2_of_3": sens["n_non_monotone_of_3"] >= 2,
    }
    n_pass = sum(1 for v in diag.values() if v)
    n_total = len(diag)

    v4 = {
        "experiment": "cutoff_clean_2026-05-29",
        "addendum_version": "v4",
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
            "summary_v3": {
                "path": SUMMARY_V3.relative_to(REPO).as_posix(),
                "sha256": _sha(SUMMARY_V3),
                "diagnostic_pass_rate": f"{base_v3['diagnostic_verdict_v3']['n_pass']}/{base_v3['diagnostic_verdict_v3']['n_total']}",
            },
        },
        "diagnostics_v4": {
            "step_19_leakage_curve": {
                "sha256": _sha(LEAKAGE),
                "n_windows": leak["n_windows"],
                "window_len_days": leak["window_len_days"],
                "opus_monotone_increasing": leak["monotonicity"]["opus_monotone_increasing"],
                "opus_monotone_decreasing": leak["monotonicity"]["opus_monotone_decreasing"],
                "opus_paired_deltas": leak["monotonicity"]["opus_paired_deltas"],
                "max_window_to_window_jump_opus": leak["monotonicity"]["max_window_to_window_jump_opus"],
                "interpretation_summary": leak["interpretation"],
                "verdict": (
                    "Step 10 Feb→Mar Brier asymmetry is NOT a smooth temporal gradient. "
                    "Across 7 overlapping 14d windows the Opus paired Brier swings both "
                    "directions (e.g. late-Apr window n=58 resurges to Brier 0.082, "
                    "edge -0.135 vs market). This argues against a monotone cutoff-leakage "
                    "model in favor of cluster-/event-driven degradation. NOT a leakage "
                    "elimination claim — Step 17 forward protocol is still required."
                ),
            },
            "step_20_leakage_curve_sensitivity": {
                "sha256": _sha(SENS),
                "window_sizes_tested": sens["window_sizes_tested"],
                "verdicts_summary": sens["verdicts_summary"],
                "n_non_monotone_of_3": sens["n_non_monotone_of_3"],
                "finding_robust": sens["finding_robust"],
                "interpretation_summary": sens["interpretation"],
                "verdict": (
                    "Step 19's non-monotone pattern holds at 3/3 window sizes "
                    "(7d / 14d / 21d, non-overlapping stride). Not a 14d-stride artifact. "
                    "Sign-flip counts: 7d=5, 14d=2, 21d=2 — consistent with cluster-driven "
                    "noise rather than smooth gradient. Robustness check PASSES."
                ),
            },
        },
        "diagnostic_verdict_v4": {
            "n_pass": n_pass,
            "n_total": n_total,
            "all_pass": n_pass == n_total,
            "details": diag,
        },
        "alpha_identity_statement_v4": (
            "Adds diagnostic specificity to v3: the Feb→Mar Brier asymmetry that "
            "originally raised cutoff-leakage suspicion is NOT a smooth temporal "
            "decay — it is cluster-/event-driven, robust across {7d, 14d, 21d} "
            "window sizes. This narrows but does not eliminate leakage risk. "
            "The structural elimination (Step 17 forward protocol, register_at "
            "= 2026-05-30T00:00:00Z) remains the only mechanism that makes "
            "outcomes truly OOS w.r.t. Opus 4.7's January 2026 cutoff. "
            "Status: paper-only. v4 does NOT promote to Nostr/IPFS."
        ),
        "honest_caveats_v4": [
            "Non-monotone Brier curve does NOT rule out leakage — it only argues "
            "against the smooth-temporal-gradient model. Cluster-driven degradation "
            "is fully compatible with category-/event-specific leakage.",
            "Step 19 windows were sliced on closedTime from the same v1 cohort — "
            "this is descriptive, not orthogonal evidence.",
            "Step 20 non-overlapping stride reduces redundancy vs Step 19 but uses "
            "the same underlying paired observations.",
            "Small windows (n_paired < 10) yield unstable Brier estimates — the "
            "late-Apr resurgence (n=58) is a real observation, but single windows "
            "at boundary sample sizes warrant skepticism.",
            "The Step 17 forward protocol remains the only structural leakage "
            "elimination mechanism. Steps 19-20 are diagnostics ABOUT the leakage "
            "model, not a substitute for OOS validation.",
            "PAPER-ONLY persists. v4 does NOT promote to Nostr/IPFS.",
            "Live-capital decision requires `debate --critique` per global rule.",
        ],
    }

    OUT.write_text(json.dumps(v4, indent=2, ensure_ascii=False))

    # Append v4 artifacts to freeze.json
    fz = json.loads(FREEZE.read_text())
    existing = {a["path"] for a in fz["artifacts"]}
    v4_artifacts = [
        ("leakage_curve_v4",             LEAKAGE),
        ("leakage_curve_sensitivity_v4", SENS),
        ("summary_v4",                   OUT),
    ]
    added = []
    for label, p in v4_artifacts:
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
    fz["addendum_v4_frozen_at_utc"] = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    fz["notes"].append(
        "Addendum v4 2026-05-29: leakage_curve (Step 19) + leakage_curve_sensitivity (Step 20) + summary_v4 sealed."
    )
    FREEZE.write_text(json.dumps(fz, indent=2) + "\n")

    print(json.dumps(v4, indent=2, ensure_ascii=False))
    print()
    print(f"✅ summary_v4.json written ({len(json.dumps(v4))} bytes)")
    print(f"✅ freeze.json updated. v4 artifacts added: {len(added)}")
    for r in added:
        print(f"  + {r}")
    print(f"Total artifacts now: {len(fz['artifacts'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
