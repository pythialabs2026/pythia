"""Step 12 — Kelly cap sensitivity.

Re-run the virtual P&L engine with stake cap ∈ {0.01, 0.02, 0.05, 0.10, 0.20}.
The 5% cap binds 100% of bets in the locked run, meaning effective sizing
is flat 5% — not Kelly. This sweep measures how much the cap actually matters.

Output: kelly_sensitivity.json
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
BASELINE = DIR / "baseline_prices.jsonl"
OUT = DIR / "kelly_sensitivity.json"

BANKROLL_0 = 1000.0
EDGE_MIN = 0.05
SLIPPAGE = 0.01
FEE = 0.02

CAPS = [0.01, 0.02, 0.05, 0.10, 0.20]


def _parse_close(ts: str):
    from datetime import datetime as _dt
    if ts.endswith("+00"):
        ts = ts.replace("+00", "+00:00")
    if "T" in ts:
        return _dt.fromisoformat(ts.replace("Z", "+00:00"))
    return _dt.fromisoformat(ts)


def _simulate(rows: list[dict], stake_cap: float) -> dict:
    bankroll = BANKROLL_0
    nav_log = []
    last_day = None; prev_nav = BANKROLL_0
    nbets = 0; nwins = 0; ncapped = 0
    for r in rows:
        if r["p_market"] is None:
            continue
        edge = r["p_opus"] - r["p_market"]
        if abs(edge) < EDGE_MIN:
            continue
        side = "YES" if edge > 0 else "NO"
        m = r["p_market"]
        denom = min(m, 1.0 - m)
        if denom <= 1e-9: continue
        kelly_frac = 0.5 * abs(edge) / denom
        stake_frac = min(kelly_frac, stake_cap)
        if abs(stake_frac - stake_cap) < 1e-9 and kelly_frac > stake_cap:
            ncapped += 1
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
        nbets += 1
        if net > 0: nwins += 1
        day = r["closed_dt"].date().isoformat()
        if last_day is not None and day != last_day:
            nav_log.append((last_day, prev_nav))
        prev_nav = bankroll
        last_day = day
    if last_day is not None:
        nav_log.append((last_day, prev_nav))

    # Sharpe + DD
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
        "stake_cap":   stake_cap,
        "n_bets":      nbets,
        "n_winners":   nwins,
        "win_rate":    round(nwins / nbets, 4) if nbets else None,
        "n_capped":    ncapped,
        "cap_bind_rate": round(ncapped / nbets, 4) if nbets else None,
        "final_nav":   round(bankroll, 2),
        "roi":         round(bankroll / BANKROLL_0 - 1.0, 6),
        "sharpe_daily": round(sharpe, 4) if sharpe is not None else None,
        "max_drawdown": round(max_dd, 4),
    }


def main() -> int:
    cohort = {str(r["id"]): r for r in (json.loads(l) for l in COHORT.open())}
    preds = {str(r["market_id"]): r for r in (json.loads(l) for l in PRED.open())}
    base = {str(r["market_id"]): r for r in (json.loads(l) for l in BASELINE.open())}

    rows = []
    for mid, c in cohort.items():
        rows.append({
            "mid": mid, "y": c["y"],
            "p_opus": preds[mid]["p_yes"],
            "p_market": base[mid]["market_p_at_freeze"],
            "closed_dt": _parse_close(c["closedTime"]),
        })
    rows.sort(key=lambda r: r["closed_dt"])

    sweep = [_simulate(rows, cap) for cap in CAPS]

    # Reference: original 5% result for cross-check
    ref = next(s for s in sweep if abs(s["stake_cap"] - 0.05) < 1e-9)

    out = {
        "experiment": "cutoff_clean_2026-05-29",
        "analysis": "kelly_cap_sensitivity",
        "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "edge_threshold": EDGE_MIN,
        "fee": FEE,
        "slippage": SLIPPAGE,
        "bankroll_start": BANKROLL_0,
        "stake_caps_tested": CAPS,
        "sweep": sweep,
        "reference_5pct_check": {
            "final_nav": ref["final_nav"],
            "matches_locked_summary": abs(ref["final_nav"] - 27609.07) < 0.5,
        },
        "interpretation": (
            "Lower caps → tighter risk, lower NAV but possibly higher risk-adjusted return. "
            "Higher caps → more leverage on Kelly's ideal sizing if it's accurate, or amplified blowup if it's not."
        ),
        "caveats": [
            "Sweep is in-sample — same cohort, same prices, only sizing differs. NO new data.",
            "All paths assume identical CLOB execution; real fills would diverge by cap (size impact).",
            "Sharpe computed on calendar-day NAV samples taken at last bet of each day.",
            "Original locked run used 5% — only that single number is the 박제 result.",
        ],
    }

    OUT.write_text(json.dumps(out, indent=2, ensure_ascii=False))
    print(json.dumps(out, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
