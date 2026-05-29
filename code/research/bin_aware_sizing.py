"""Step 16 — Bin-aware sizing.

Use Step 15's reliability map to scale Kelly stake by calibration quality.

Sizing rule:
    stake_frac = min(stake_cap, 0.5 * |edge| / min(p_m, 1-p_m)) * reliability_factor

where reliability_factor for the bin Opus's p_yes falls into is:
    well_calibrated (|gap|<0.05)       → 1.00 full Kelly
    moderately      (|gap|<0.15)       → 0.50 half
    miscalibrated   (|gap|>=0.15)      → 0.00 skip
    profitable_bin override            → keep factor but require n_obs>=10 (else skip)

We compare against:
  A) flat 5% cap (locked summary baseline, NAV=$27609.07)
  B) bin-aware sizing

Output: data/research/backtests/cutoff_clean_2026-05-29/bin_aware_sizing.json
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
CAL = DIR / "calibration_deepdive.json"
OUT = DIR / "bin_aware_sizing.json"

BANKROLL_0 = 1000.0
EDGE_MIN = 0.05
SLIPPAGE = 0.01
FEE = 0.02
STAKE_CAP = 0.05
MIN_BIN_N = 10  # bins with fewer cohort obs treated as untrusted (factor=0.0)


def _parse_close(ts: str):
    if ts.endswith("+00"):
        ts = ts.replace("+00", "+00:00")
    if "T" in ts:
        return datetime.fromisoformat(ts.replace("Z", "+00:00"))
    return datetime.fromisoformat(ts)


def _bin_idx(p: float) -> int:
    if p >= 1.0:
        return 9
    if p < 0.0:
        return 0
    return int(p * 10)


def _reliability_factor(bin_info: dict) -> float:
    if bin_info is None or bin_info.get("n_obs", 0) < MIN_BIN_N:
        return 0.0
    q = bin_info.get("calibration_quality")
    if q == "well_calibrated":
        return 1.0
    if q == "moderately_calibrated":
        return 0.5
    return 0.0  # miscalibrated


def _simulate(rows: list[dict], reliability_lookup) -> dict:
    bankroll = BANKROLL_0
    nav_log = []
    last_day = None; prev_nav = BANKROLL_0
    nbets = 0; nwins = 0
    n_filtered_by_reliability = 0
    total_stake = 0.0; total_net = 0.0
    bin_contrib = {i: {"n": 0, "pnl": 0.0} for i in range(10)}

    for r in rows:
        if r["p_market"] is None:
            continue
        edge = r["p_opus"] - r["p_market"]
        if abs(edge) < EDGE_MIN:
            continue
        idx = _bin_idx(r["p_opus"])
        factor = reliability_lookup(idx)
        if factor <= 0.0:
            n_filtered_by_reliability += 1
            continue
        side = "YES" if edge > 0 else "NO"
        m = r["p_market"]
        denom = min(m, 1.0 - m)
        if denom <= 1e-9: continue
        kelly_raw = 0.5 * abs(edge) / denom
        stake_frac = min(kelly_raw, STAKE_CAP) * factor
        if stake_frac <= 0:
            continue
        stake = bankroll * stake_frac
        if stake <= 0: continue
        if side == "YES":
            entry = min(m * (1 + SLIPPAGE), 0.999)
            shares = stake / entry
            payout = shares * r["y"]
        else:
            entry = min((1 - m) * (1 + SLIPPAGE), 0.999)
            shares = stake / entry
            payout = shares * (1 - r["y"])
        fee = stake * FEE
        net = payout - stake - fee
        bankroll += net
        nbets += 1; total_stake += stake; total_net += net
        if net > 0: nwins += 1
        bin_contrib[idx]["n"] += 1
        bin_contrib[idx]["pnl"] += net
        day = r["closed_dt"].date().isoformat()
        if last_day is not None and day != last_day:
            nav_log.append((last_day, prev_nav))
        prev_nav = bankroll
        last_day = day
    if last_day is not None:
        nav_log.append((last_day, prev_nav))

    if nav_log:
        navs = [BANKROLL_0] + [v for _, v in nav_log]
        rets = [navs[i] / navs[i-1] - 1.0 for i in range(1, len(navs)) if navs[i-1] > 0]
        if len(rets) >= 2:
            mu = statistics.mean(rets); sd = statistics.stdev(rets)
            sharpe = (mu / sd * math.sqrt(365)) if sd > 0 else None
        else:
            sharpe = None
        peak = navs[0]; max_dd = 0.0
        for v in navs:
            peak = max(peak, v)
            dd = (peak - v) / peak if peak > 0 else 0.0
            max_dd = max(max_dd, dd)
    else:
        sharpe = None; max_dd = 0.0

    return {
        "n_bets_taken": nbets,
        "n_winners": nwins,
        "win_rate": round(nwins / nbets, 4) if nbets else None,
        "n_filtered_by_reliability": n_filtered_by_reliability,
        "total_stake": round(total_stake, 2),
        "total_net_pnl": round(total_net, 2),
        "final_nav": round(bankroll, 2),
        "roi": round(bankroll / BANKROLL_0 - 1.0, 6),
        "sharpe_daily": round(sharpe, 4) if sharpe is not None else None,
        "max_drawdown": round(max_dd, 4),
        "bin_contributions": {
            str(i): {"n_bets": v["n"], "net_pnl": round(v["pnl"], 2)}
            for i, v in bin_contrib.items() if v["n"] > 0
        },
    }


def main() -> int:
    cohort = {str(r["id"]): r for r in (json.loads(l) for l in COHORT.open())}
    preds = {str(r["market_id"]): r for r in (json.loads(l) for l in PRED.open())}
    base = {str(r["market_id"]): r for r in (json.loads(l) for l in BASE.open())}
    cal = json.loads(CAL.read_text())
    bins_by_idx = {b["bin_idx"]: b for b in cal["bins"]}

    factor_map = {i: _reliability_factor(bins_by_idx.get(i)) for i in range(10)}

    def lookup(idx: int) -> float:
        return factor_map.get(idx, 0.0)

    rows = []
    for mid, c in cohort.items():
        rows.append({
            "mid": mid, "y": c["y"],
            "p_opus": preds[mid]["p_yes"],
            "p_market": base[mid]["market_p_at_freeze"],
            "closed_dt": _parse_close(c["closedTime"]),
        })
    rows.sort(key=lambda r: r["closed_dt"])

    # Baseline: flat (factor=1 always; pure 5% cap, no reliability gate)
    baseline = _simulate(rows, lambda i: 1.0)
    # Bin-aware
    bin_aware = _simulate(rows, lookup)

    delta_nav = bin_aware["final_nav"] - baseline["final_nav"]
    delta_sharpe = (
        (bin_aware["sharpe_daily"] - baseline["sharpe_daily"])
        if (bin_aware["sharpe_daily"] is not None and baseline["sharpe_daily"] is not None) else None
    )
    delta_dd = bin_aware["max_drawdown"] - baseline["max_drawdown"]

    out = {
        "experiment": "cutoff_clean_2026-05-29",
        "analysis": "bin_aware_sizing",
        "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "reliability_policy": {
            "min_bin_n": MIN_BIN_N,
            "well_calibrated_factor": 1.0,
            "moderately_calibrated_factor": 0.5,
            "miscalibrated_factor": 0.0,
            "factor_map_by_bin": {str(i): factor_map[i] for i in range(10)},
        },
        "sim_params": {
            "bankroll_0": BANKROLL_0, "edge_min": EDGE_MIN,
            "stake_cap": STAKE_CAP, "fee": FEE, "slippage": SLIPPAGE,
        },
        "baseline_flat_5pct": baseline,
        "bin_aware": bin_aware,
        "delta_bin_aware_minus_baseline": {
            "delta_final_nav": round(delta_nav, 2),
            "delta_sharpe":    round(delta_sharpe, 4) if delta_sharpe is not None else None,
            "delta_max_drawdown": round(delta_dd, 4),
            "n_skipped_due_to_low_reliability": bin_aware["n_filtered_by_reliability"],
        },
        "interpretation": (
            f"Bin-aware: NAV ${bin_aware['final_nav']} vs flat 5% NAV ${baseline['final_nav']} "
            f"(Δ {delta_nav:+.2f}). Sharpe {bin_aware['sharpe_daily']} vs {baseline['sharpe_daily']} "
            f"(Δ {delta_sharpe}). Max DD {bin_aware['max_drawdown']} vs {baseline['max_drawdown']} (Δ {delta_dd:+.4f}). "
            f"Filtered {bin_aware['n_filtered_by_reliability']} bets due to miscalibrated bins."
        ),
        "caveats": [
            "Reliability factors come from THE SAME COHORT we're sizing on — this is in-sample tuning, not OOS validation.",
            "True bin-aware sizing requires Out-of-Sample calibration (Step 17 protocol).",
            "Small-bin reliability is unstable; MIN_BIN_N=10 prunes bins with <10 obs to factor 0.",
            "Skipped bets may have been profitable individually; we're trading expected value for variance control.",
        ],
    }

    OUT.write_text(json.dumps(out, indent=2, ensure_ascii=False))
    print(json.dumps(out, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
